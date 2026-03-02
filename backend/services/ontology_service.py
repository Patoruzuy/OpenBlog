"""Ontology Service — manage the concept tree and browse prompts by node.

Scope rules
-----------
All service functions that return nodes or prompts accept a ``public_only``
flag (defaulting to True) or a ``workspace`` parameter.

list_tree(public_only=True):
    Returns only nodes where is_public=True, ordered by sort_order/name.

list_prompts_for_node(viewer, node, workspace=None, include_descendants=True):
    Public scope (workspace=None):
      - Only public content_ontology rows (workspace_id IS NULL).
      - Only published, public prompts (kind='prompt', workspace_id IS NULL,
        status='published').
    Workspace scope (workspace=<ws>):
      - Public + same-workspace content_ontology rows.
      - Published public prompts + published workspace prompts.
      - Cross-workspace rows excluded at query level.

Node management (create/update) requires admin or editor role.

Query pattern — no N+1
-----------------------
list_tree loads all matching nodes in ONE query, then builds the tree in
Python via a parent-id map.

list_prompts_for_node uses at most TWO queries:
  1. Descendant IDs (BFS in Python over nodes already loaded from tree).
  2. Single join: content_ontology → posts.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select

from backend.extensions import db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.user import User, UserRole

# ── Exceptions ────────────────────────────────────────────────────────────────


class OntologyError(Exception):
    """Domain error for ontology operations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── DTOs ──────────────────────────────────────────────────────────────────────


@dataclass
class NodeTreeItem:
    """A node with its children (recursive)."""

    node: OntologyNode
    children: list[NodeTreeItem] = field(default_factory=list)


# ── Slug helpers ──────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "node"


def _unique_node_slug(base: str) -> str:
    base = base[:80]
    existing = set(
        db.session.scalars(
            select(OntologyNode.slug).where(OntologyNode.slug.like(f"{base}%"))
        ).all()
    )
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


# ── Admin permission check ─────────────────────────────────────────────────────


def _require_manage(user: User) -> None:
    """Raise OntologyError 403 if user cannot manage ontology nodes."""
    if user is None or user.role.value not in UserRole.EDITOR_SET:
        raise OntologyError("Admin or editor role required.", 403)


# ── Node CRUD ─────────────────────────────────────────────────────────────────


def create_node(
    admin: User,
    slug: str,
    name: str,
    *,
    description: str | None = None,
    parent_id: int | None = None,
    sort_order: int = 0,
    is_public: bool = True,
) -> OntologyNode:
    """Create a new ontology node.

    Raises OntologyError on permission failure or duplicate slug.
    """
    _require_manage(admin)

    slug = slug.strip() or _slugify(name)
    slug = _slugify(slug)

    existing = db.session.scalar(
        select(OntologyNode).where(OntologyNode.slug == slug)
    )
    if existing is not None:
        raise OntologyError(f"Slug '{slug}' is already in use.", 409)

    if parent_id is not None:
        parent = db.session.get(OntologyNode, parent_id)
        if parent is None:
            raise OntologyError("Parent node not found.", 404)

    node = OntologyNode(
        slug=slug,
        name=name.strip(),
        description=description,
        parent_id=parent_id,
        sort_order=sort_order,
        is_public=is_public,
        created_by_user_id=admin.id,
    )
    db.session.add(node)
    db.session.flush()
    return node


def update_node(
    admin: User,
    node_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    parent_id: int | None | type[_UNSET] = None,
    sort_order: int | None = None,
    is_public: bool | None = None,
) -> OntologyNode:
    """Update an existing ontology node.

    Pass ``parent_id=None`` explicitly to clear the parent.
    Use the sentinel :data:`_UNSET` to leave parent_id unchanged.
    """
    _require_manage(admin)

    node = db.session.get(OntologyNode, node_id)
    if node is None:
        raise OntologyError("Node not found.", 404)

    if name is not None:
        node.name = name.strip()
    if description is not None:
        node.description = description or None
    if parent_id is not _UNSET:
        if parent_id == node_id:
            raise OntologyError("A node cannot be its own parent.")
        node.parent_id = parent_id  # type: ignore[assignment]
    if sort_order is not None:
        node.sort_order = sort_order
    if is_public is not None:
        node.is_public = is_public

    node.updated_at = datetime.now(UTC)
    db.session.flush()
    return node


class _UNSET:  # noqa: N801
    """Sentinel for 'not provided' in update_node."""


# ── Tree query ─────────────────────────────────────────────────────────────────


def list_tree(*, public_only: bool = True) -> list[NodeTreeItem]:
    """Return the full ontology tree as nested :class:`NodeTreeItem` DTOs.

    Nodes are loaded in a single query.  The tree is assembled in Python via
    a parent-id map.  Root nodes (parent_id IS NULL) are returned at the top
    level, ordered by sort_order then name.
    """
    stmt = select(OntologyNode).order_by(OntologyNode.sort_order, OntologyNode.name)
    if public_only:
        stmt = stmt.where(OntologyNode.is_public.is_(True))

    all_nodes = db.session.scalars(stmt).all()

    # Build id → NodeTreeItem map
    items: dict[int, NodeTreeItem] = {n.id: NodeTreeItem(node=n) for n in all_nodes}

    roots: list[NodeTreeItem] = []
    for item in items.values():
        pid = item.node.parent_id
        if pid is None or pid not in items:
            roots.append(item)
        else:
            items[pid].children.append(item)

    return roots


def get_node_by_slug(slug: str) -> OntologyNode | None:
    """Return a node by slug or None."""
    return db.session.scalar(
        select(OntologyNode).where(OntologyNode.slug == slug)
    )


def get_all_descendant_ids(
    node_id: int, *, public_only: bool = True
) -> list[int]:
    """Return *node_id* plus all descendant node IDs (BFS, bounded to tree).

    Uses a single query to load all nodes then traverses in Python.
    """
    stmt = select(OntologyNode.id, OntologyNode.parent_id)
    if public_only:
        stmt = stmt.where(OntologyNode.is_public.is_(True))
    rows = db.session.execute(stmt).all()

    # parent_id → [child_id]
    children_map: dict[int | None, list[int]] = {}
    for nid, pid in rows:
        children_map.setdefault(pid, []).append(nid)

    result: list[int] = []
    queue: deque[int] = deque([node_id])
    while queue:
        current = queue.popleft()
        result.append(current)
        for child_id in children_map.get(current, []):
            queue.append(child_id)

    return result


# ── Prompt listing ────────────────────────────────────────────────────────────


def list_prompts_for_node(
    viewer: object,
    node: OntologyNode,
    workspace: object | None = None,
    *,
    include_descendants: bool = True,
    limit: int = 50,
) -> list[Post]:
    """Return published prompts mapped to *node* (and optionally descendants).

    Scope rules
    -----------
    Public scope (workspace=None):
      - content_ontology.workspace_id IS NULL
      - Post: status=published, workspace_id IS NULL, kind='prompt'

    Workspace scope (workspace=<ws>):
      - content_ontology.workspace_id IS NULL OR = ws.id
      - Post: status=published, (workspace_id IS NULL OR = ws.id), kind='prompt'

    Two queries maximum regardless of family size.
    """
    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]
    is_public = public_only = ws_id is None

    # ── Query 1: descendant node IDs ──────────────────────────────────────
    if include_descendants:
        node_ids = get_all_descendant_ids(node.id, public_only=is_public)
    else:
        node_ids = [node.id]

    if not node_ids:
        return []

    # ── Query 2: prompts via content_ontology → posts ─────────────────────
    mapping_scope = (
        ContentOntology.workspace_id.is_(None)
        if public_only
        else or_(
            ContentOntology.workspace_id.is_(None),
            ContentOntology.workspace_id == ws_id,
        )
    )
    post_scope = (
        Post.workspace_id.is_(None)
        if public_only
        else or_(Post.workspace_id.is_(None), Post.workspace_id == ws_id)
    )

    rows = db.session.scalars(
        select(Post)
        .join(ContentOntology, ContentOntology.post_id == Post.id)
        .where(
            ContentOntology.ontology_node_id.in_(node_ids),
            mapping_scope,
            Post.kind == "prompt",
            Post.status == PostStatus.published,
            post_scope,
        )
        .distinct()
        .order_by(Post.published_at.desc(), Post.id.desc())
        .limit(limit)
    ).all()

    return list(rows)
