"""Content Ontology Service — map posts to ontology nodes with scope isolation.

Scope model
-----------
Public mapping (workspace_id IS NULL):
    Visible to all.  Managed by editors/admins on public prompts.

Workspace overlay (workspace_id = ws.id):
    Visible only to workspace members.  Managed by workspace editors/owners.
    Does NOT affect what others see on the public page.

set_mappings is a replace-style operation:
    All existing mappings for the given (post, workspace) scope are deleted
    and replaced with the new node_ids.  This avoids stale mappings and
    simplifies the UI (send current full selection each time).

Permission rules
----------------
Public prompts (post.workspace_id IS NULL):
    Requires admin or editor global role.

Workspace prompts / workspace overlay (workspace != None):
    Requires workspace editor or owner membership.
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select

from backend.extensions import db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post
from backend.models.user import User, UserRole
from backend.models.workspace import WorkspaceMember, WorkspaceMemberRole

# ── Exceptions ────────────────────────────────────────────────────────────────


class ContentOntologyError(Exception):
    """Domain error for content-ontology mapping operations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Permission helpers ─────────────────────────────────────────────────────────


def _can_manage_public(user: User) -> bool:
    """True if user can manage public (workspace_id IS NULL) mappings."""
    return user is not None and user.role.value in UserRole.EDITOR_SET


def _can_manage_workspace(user: User, workspace_id: int) -> bool:
    """True if user is an editor or owner of the given workspace."""
    if user is None:
        return False
    role = db.session.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if role is None:
        return False
    return role in (WorkspaceMemberRole.owner, WorkspaceMemberRole.editor)


# ── Core operations ────────────────────────────────────────────────────────────


def set_mappings(
    user: User,
    post: Post,
    node_ids: list[int],
    workspace: object | None = None,
) -> None:
    """Replace all ontology mappings for *post* in the given scope.

    Parameters
    ----------
    user:
        The acting user (permission enforced).
    post:
        The post whose mappings are being updated.
    node_ids:
        Complete new set of ontology node IDs.  Pass [] to clear all.
    workspace:
        ``None`` for public mapping; workspace object for workspace overlay.

    Raises ContentOntologyError on permission failure or bad node IDs.
    """
    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    # ── Permission check ─────────────────────────────────────────────────
    if ws_id is None:
        if not _can_manage_public(user):
            raise ContentOntologyError("Editor or admin role required.", 403)
    else:
        if not _can_manage_workspace(user, ws_id):
            raise ContentOntologyError("Workspace editor or owner role required.", 403)

    # ── Validate node IDs (all must exist and be public) ─────────────────
    if node_ids:
        existing_ids = set(
            db.session.scalars(
                select(OntologyNode.id).where(
                    OntologyNode.id.in_(node_ids),
                    OntologyNode.is_public.is_(True),
                )
            ).all()
        )
        invalid = set(node_ids) - existing_ids
        if invalid:
            raise ContentOntologyError(
                f"Unknown or private ontology node IDs: {sorted(invalid)}"
            )

    # ── Delete existing mappings for this (post, workspace) scope ────────
    db.session.execute(
        delete(ContentOntology).where(
            ContentOntology.post_id == post.id,
            (
                ContentOntology.workspace_id.is_(None)
                if ws_id is None
                else ContentOntology.workspace_id == ws_id
            ),
        )
    )
    db.session.flush()

    # ── Insert new mappings ───────────────────────────────────────────────
    for nid in set(node_ids):  # deduplicate
        db.session.add(
            ContentOntology(
                post_id=post.id,
                ontology_node_id=nid,
                workspace_id=ws_id,
                created_by_user_id=user.id,
            )
        )
    db.session.flush()


def get_mappings_for_post(
    viewer: object,
    post: Post,
    workspace: object | None = None,
) -> list[ContentOntology]:
    """Return all content_ontology rows visible to *viewer* for *post*.

    Public scope (workspace=None):
        Returns only rows where workspace_id IS NULL.

    Workspace scope (workspace=<ws>):
        Returns public rows + workspace overlay rows for that workspace.
        Cross-workspace rows are never included.

    ``viewer`` is accepted but not currently used for access-level gating;
    workspace membership checks are the caller's responsibility.
    """
    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    scope = (
        ContentOntology.workspace_id.is_(None)
        if ws_id is None
        else or_(
            ContentOntology.workspace_id.is_(None),
            ContentOntology.workspace_id == ws_id,
        )
    )

    rows = db.session.scalars(
        select(ContentOntology)
        .where(ContentOntology.post_id == post.id, scope)
        .order_by(ContentOntology.ontology_node_id)
    ).all()

    return list(rows)


def get_mapping_ids_for_post(
    post: Post,
    workspace: object | None = None,
) -> list[int]:
    """Return a list of node IDs mapped to *post* in the given scope.

    Convenience function for pre-selecting checkboxes in the mapping UI.
    """
    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    scope = (
        ContentOntology.workspace_id.is_(None)
        if ws_id is None
        else or_(
            ContentOntology.workspace_id.is_(None),
            ContentOntology.workspace_id == ws_id,
        )
    )

    return list(
        db.session.scalars(
            select(ContentOntology.ontology_node_id).where(
                ContentOntology.post_id == post.id, scope
            )
        ).all()
    )


def bulk_get_mapping_ids(
    post_ids: list[int],
    workspace: object | None = None,
) -> dict[int, list[int]]:
    """Return {post_id: [node_id, ...]} for all given post_ids.

    Used by list views to avoid N+1 queries when showing ontology chips.
    """
    if not post_ids:
        return {}

    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    scope = (
        ContentOntology.workspace_id.is_(None)
        if ws_id is None
        else or_(
            ContentOntology.workspace_id.is_(None),
            ContentOntology.workspace_id == ws_id,
        )
    )

    rows = db.session.execute(
        select(ContentOntology.post_id, ContentOntology.ontology_node_id).where(
            ContentOntology.post_id.in_(post_ids), scope
        )
    ).all()

    result: dict[int, list[int]] = {pid: [] for pid in post_ids}
    for post_id, node_id in rows:
        result[post_id].append(node_id)
    return result
