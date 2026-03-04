"""Content-link service — Knowledge Graph relationships.

Scope isolation rules
---------------------
1. public → public:          allowed  (workspace_id=None on both; link.workspace_id=None)
2. workspace → same WS:      allowed  (link.workspace_id = from_post.workspace_id)
3. workspace → public:       allowed  (to_post.workspace_id may differ; link scoped to WS)
4. public → workspace:       FORBIDDEN
5. workspace A → workspace B: FORBIDDEN

Rule derivation
---------------
The ``workspace_id`` on the link is always inherited from ``from_post``:
  - from_post.workspace_id IS NULL  → link.workspace_id IS NULL
  - from_post.workspace_id = ws.id  → link.workspace_id = ws.id

For rule 3: from_post (workspace) may point to to_post (public) or same workspace.
For rule 4/5: if from_post is public (workspace_id=None) and to_post is workspace-scoped → reject.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from sqlalchemy import and_, select

from backend.extensions import db
from backend.models.content_link import VALID_LINK_TYPES, ContentLink
from backend.models.post import Post
from backend.models.user import User

# ── Exception ─────────────────────────────────────────────────────────────────


class ContentLinkError(Exception):
    """Domain-level error for content-link operations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_post_or_404(post_id: int) -> Post:
    post = db.session.get(Post, post_id)
    if post is None:
        raise ContentLinkError("Post not found.", 404)
    return post


def _get_post_by_slug_or_404(slug: str) -> Post:
    post = db.session.execute(
        select(Post).where(Post.slug == slug)
    ).scalar_one_or_none()
    if post is None:
        raise ContentLinkError("Post not found.", 404)
    return post


def _can_manage(user: User, workspace_id: int | None) -> bool:
    """True if user can add/remove links in the given scope."""
    from backend.models.user import UserRole
    from backend.models.workspace import WorkspaceMember, WorkspaceMemberRole

    if user.role in (UserRole.editor, UserRole.admin):
        return True
    if workspace_id is not None:
        member = db.session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
                WorkspaceMember.role.in_(
                    [WorkspaceMemberRole.editor, WorkspaceMemberRole.owner]
                ),
            )
        ).scalar_one_or_none()
        return member is not None
    return False


def _enforce_scope(from_post: Post, to_post: Post) -> None:
    """Raise ContentLinkError if the directed edge violates isolation rules."""
    from_ws = from_post.workspace_id
    to_ws = to_post.workspace_id

    # Rule 4: public cannot point to workspace content.
    if from_ws is None and to_ws is not None:
        raise ContentLinkError(
            "Public content cannot link to workspace-scoped content.", 400
        )

    # Rule 5: workspace A cannot point to workspace B.
    if from_ws is not None and to_ws is not None and from_ws != to_ws:
        raise ContentLinkError("Cannot link across different workspaces.", 400)

    # Rules 1, 2, 3 pass.


# ── Public API ────────────────────────────────────────────────────────────────


def add_link(
    user: User,
    from_post: Post,
    to_post: Post,
    link_type: str,
) -> ContentLink:
    """Create a directed relationship edge.

    Parameters
    ----------
    user:        Must have editor+ rights in the relevant scope.
    from_post:   Source post.
    to_post:     Target post.
    link_type:   One of VALID_LINK_TYPES.

    Raises
    ------
    ContentLinkError(400) for invalid type, duplicate, or scope violation.
    ContentLinkError(403) for insufficient permissions.

    Returns
    -------
    ContentLink (not yet committed).
    """
    if link_type not in VALID_LINK_TYPES:
        raise ContentLinkError(
            f"Invalid link_type '{link_type}'. "
            f"Valid values: {', '.join(VALID_LINK_TYPES)}.",
            400,
        )

    _enforce_scope(from_post, to_post)

    workspace_id: int | None = from_post.workspace_id

    if not _can_manage(user, workspace_id):
        raise ContentLinkError("You do not have permission to add links here.", 403)

    # Self-links are nonsensical.
    if from_post.id == to_post.id:
        raise ContentLinkError("A post cannot link to itself.", 400)

    # Duplicate check (handles NULL workspace_id correctly in Python).
    existing = db.session.execute(
        select(ContentLink).where(
            ContentLink.from_post_id == from_post.id,
            ContentLink.to_post_id == to_post.id,
            ContentLink.link_type == link_type,
            (
                ContentLink.workspace_id.is_(None)
                if workspace_id is None
                else ContentLink.workspace_id == workspace_id
            ),
        )
    ).scalar_one_or_none()
    if existing:
        raise ContentLinkError("This relationship already exists.", 409)

    link = ContentLink(
        from_post_id=from_post.id,
        to_post_id=to_post.id,
        link_type=link_type,
        workspace_id=workspace_id,
        created_by_user_id=user.id,
    )
    db.session.add(link)
    return link


def remove_link(user: User, link_id: int) -> None:
    """Delete a content link by id.

    Raises ContentLinkError(404) if not found, ContentLinkError(403) if
    the user does not have management rights.
    """
    link = db.session.get(ContentLink, link_id)
    if link is None:
        raise ContentLinkError("Link not found.", 404)

    if not _can_manage(user, link.workspace_id):
        raise ContentLinkError("You do not have permission to remove this link.", 403)

    db.session.delete(link)


def list_links_for_post(
    post: Post,
    workspace_id: int | None,
    direction: Literal["outgoing", "incoming", "both"] = "both",
) -> list[ContentLink]:
    """Return links associated with *post* in a given scope.

    The *workspace_id* parameter controls scope isolation:
    - Pass ``None`` to retrieve public-layer links only.
    - Pass a workspace int to retrieve workspace-layer links only.
    """
    from sqlalchemy.orm import joinedload

    base_filter = (
        ContentLink.workspace_id.is_(None)
        if workspace_id is None
        else ContentLink.workspace_id == workspace_id
    )

    conditions = []
    if direction in ("outgoing", "both"):
        conditions.append(ContentLink.from_post_id == post.id)
    if direction in ("incoming", "both"):
        conditions.append(ContentLink.to_post_id == post.id)

    if not conditions:
        return []

    combined = (
        conditions[0] if len(conditions) == 1 else (conditions[0] | conditions[1])
    )

    rows = (
        db.session.execute(
            select(ContentLink)
            .where(and_(base_filter, combined))
            .options(
                joinedload(ContentLink.from_post),
                joinedload(ContentLink.to_post),
            )
            .order_by(ContentLink.created_at)
        )
        .scalars()
        .unique()
        .all()
    )

    return list(rows)


def list_links_grouped(
    post: Post,
    workspace_id: int | None,
) -> dict[str, dict[str, list[ContentLink]]]:
    """Return links grouped by direction then link_type.

    Returns
    -------
    {
      "outgoing": {link_type: [ContentLink, ...]},
      "incoming": {link_type: [ContentLink, ...]},
    }
    """
    links = list_links_for_post(post, workspace_id, direction="both")
    result: dict[str, dict[str, list[ContentLink]]] = {
        "outgoing": defaultdict(list),
        "incoming": defaultdict(list),
    }
    for link in links:
        if link.from_post_id == post.id:
            result["outgoing"][link.link_type].append(link)
        else:
            result["incoming"][link.link_type].append(link)
    # Convert to plain dicts for template friendliness.
    return {
        "outgoing": dict(result["outgoing"]),
        "incoming": dict(result["incoming"]),
    }


def get_link_or_none(link_id: int) -> ContentLink | None:
    """Fetch a single link by PK."""
    return db.session.get(ContentLink, link_id)
