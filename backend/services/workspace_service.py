"""Workspace service — all business logic for private workspace containers.

Permission enforcement strategy
--------------------------------
Every public method that loads workspace-scoped content accepts a *user*
argument and calls :func:`user_has_workspace_access` **before** returning
any data.  The service layer is the final authority — routes call helpers
here rather than re-implementing checks.

Fail-closed rule
----------------
If the workspace does not exist **or** the user has no membership, the helpers
return ``None`` (or ``False``).  Routes convert ``None`` to ``abort(404)`` so
no existence information is revealed to non-members.

Isolation guarantee
-------------------
All public-layer queries use ``Post.workspace_id.is_(None)``.  The workspace
layer uses ``Post.workspace_id == workspace.id``.  The two scopes never mix.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from flask import abort
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.post_release_note import PostReleaseNote
from backend.models.post_version import PostVersion
from backend.models.revision import Revision, RevisionStatus
from backend.models.tag import Tag
from backend.models.user import User
from backend.models.workspace import (
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from backend.utils.markdown import invalidate_html_cache, reading_time_minutes
from backend.utils.validation import validate_url


# ── Slug helpers ──────────────────────────────────────────────────────────────


def _ws_slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "workspace"


def _unique_workspace_slug(base: str) -> str:
    existing = set(
        db.session.scalars(
            select(Workspace.slug).where(Workspace.slug.like(f"{base}%"))
        ).all()
    )
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


def _doc_slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "untitled"


def _unique_doc_slug(workspace_id: int, base: str) -> str:
    existing = set(
        db.session.scalars(
            select(Post.slug).where(
                Post.workspace_id == workspace_id,
                Post.slug.like(f"{base}%"),
            )
        ).all()
    )
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


# ── Lookup helpers ────────────────────────────────────────────────────────────


def get_workspace_by_slug(slug: str) -> Workspace | None:
    """Return the :class:`~backend.models.workspace.Workspace` for *slug*, or
    ``None`` if it does not exist.

    Does **not** enforce any access check — callers must call
    :func:`user_has_workspace_access` separately, or use the higher-level
    :func:`get_workspace_for_user` helper.
    """
    return db.session.scalar(select(Workspace).where(Workspace.slug == slug))


def get_member(workspace: Workspace, user: User) -> WorkspaceMember | None:
    """Return the :class:`WorkspaceMember` row for *user* in *workspace*,
    or ``None`` when the user is not a member.
    """
    return db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )


def user_has_workspace_access(
    user: User | None,
    workspace: Workspace,
    *,
    required_role: WorkspaceMemberRole | None = None,
) -> bool:
    """Return ``True`` when *user* may access *workspace*.

    Parameters
    ----------
    user:
        The authenticated user.  ``None`` always returns ``False``.
    workspace:
        The workspace being accessed.
    required_role:
        Minimum role required.  ``None`` means any membership is sufficient
        (i.e. viewer-level access).

    Permission table
    ----------------
    +-------------+--------------------------------------------------+
    | Role        | Allowed operations                               |
    +=============+==================================================+
    | owner       | everything (create/edit docs, manage members,    |
    |             | accept revisions, delete workspace)              |
    +-------------+--------------------------------------------------+
    | editor      | create/edit docs, accept revisions               |
    +-------------+--------------------------------------------------+
    | contributor | submit revisions only                            |
    +-------------+--------------------------------------------------+
    | viewer      | read-only                                        |
    +-------------+--------------------------------------------------+
    """
    if user is None:
        return False
    member = get_member(workspace, user)
    if member is None:
        return False
    if required_role is None:
        return True
    return member.role.meets(required_role)


def get_workspace_for_user(
    slug: str,
    user: User | None,
    *,
    required_role: WorkspaceMemberRole | None = None,
) -> Workspace:
    """Look up workspace by *slug* and gate on *user* membership.

    This is the **only** correct way to resolve a workspace inside route
    handlers.  Returns the :class:`Workspace` on success.  Calls
    ``abort(404)`` on failure — intentionally using 404 (not 403) to avoid
    revealing workspace existence to non-members.

    Parameters
    ----------
    slug:
        URL slug of the workspace.
    user:
        Currently authenticated user (``None`` triggers 404).
    required_role:
        When supplied, the user must hold *at least* this role.  Use
        :attr:`WorkspaceMemberRole.editor` for write operations and
        :attr:`WorkspaceMemberRole.contributor` for revision submission.
    """
    workspace = get_workspace_by_slug(slug)
    if workspace is None:
        abort(404)
    if not user_has_workspace_access(user, workspace, required_role=required_role):
        abort(404)
    return workspace  # type: ignore[return-value]  # abort() never returns


# ── Workspace CRUD ─────────────────────────────────────────────────────────────


def create_workspace(
    *,
    name: str,
    owner: User,
    description: str | None = None,
    slug: str | None = None,
) -> Workspace:
    """Create a new workspace and add *owner* as the ``owner`` member.

    The owner :class:`WorkspaceMember` record is created atomically in the
    same transaction.  Callers must commit after this call.
    """
    base = _ws_slugify(slug or name)
    final_slug = _unique_workspace_slug(base)

    workspace = Workspace(
        slug=final_slug,
        name=name.strip(),
        description=description,
        owner_id=owner.id,
    )
    db.session.add(workspace)
    db.session.flush()  # obtain workspace.id before creating the member row

    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=owner.id,
        role=WorkspaceMemberRole.owner,
    )
    db.session.add(member)
    return workspace


def add_member(
    workspace: Workspace,
    user: User,
    role: WorkspaceMemberRole,
) -> WorkspaceMember:
    """Add *user* to *workspace* with *role*.  Callers must commit."""
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
    )
    db.session.add(member)
    return member


def list_user_workspaces(user: User) -> list[Workspace]:
    """Return all workspaces the user is a member of, newest first."""
    return list(
        db.session.scalars(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user.id)
            .order_by(Workspace.created_at.desc())
        )
    )


# ── Document CRUD ─────────────────────────────────────────────────────────────


def list_workspace_documents(workspace: Workspace) -> list[Post]:
    """Return all documents inside *workspace*, newest-updated first.

    The caller MUST have already verified membership via
    :func:`get_workspace_for_user` before calling this method.
    """
    from sqlalchemy.orm import selectinload  # noqa: PLC0415

    return list(
        db.session.scalars(
            select(Post)
            .where(Post.workspace_id == workspace.id)
            .options(joinedload(Post.author), selectinload(Post.tags))
            .order_by(Post.updated_at.desc())
        ).unique()
    )


def create_workspace_document(
    *,
    workspace: Workspace,
    author: User,
    title: str,
    markdown_body: str = "",
    tag_names: list[str] | None = None,
    seo_description: str | None = None,
    slug: str | None = None,
) -> Post:
    """Create a new workspace document (Post with workspace_id set).

    The post starts in ``draft`` status.  Callers must commit after this
    call.  The caller MUST hold at least the ``editor`` role.
    """
    from backend.services.post_service import _resolve_tags  # noqa: PLC0415

    base = _doc_slugify(slug or title)
    final_slug = _unique_doc_slug(workspace.id, base)

    post = Post(
        title=title.strip(),
        slug=final_slug,
        markdown_body=markdown_body,
        status=PostStatus.draft,
        author_id=author.id,
        workspace_id=workspace.id,
        seo_description=seo_description,
        reading_time_minutes=reading_time_minutes(markdown_body),
    )
    db.session.add(post)

    if tag_names:
        post.tags = _resolve_tags(tag_names)

    return post


def get_workspace_document(
    workspace: Workspace,
    slug: str,
) -> Post | None:
    """Return the document with *slug* inside *workspace*, or ``None``.

    The caller MUST have already verified membership via
    :func:`get_workspace_for_user` before calling this method.
    """
    return db.session.scalar(
        select(Post)
        .where(
            Post.workspace_id == workspace.id,
            Post.slug == slug,
        )
        .options(
            joinedload(Post.author),
            joinedload(Post.tags),
        )
    )


def update_workspace_document(
    post: Post,
    *,
    title: str | None = None,
    markdown_body: str | None = None,
    seo_description: str | None = None,
    tag_names: list[str] | None = None,
) -> Post:
    """Apply in-place edits to a workspace document.

    Callers must hold at least the ``editor`` role and commit after this
    call.  HTML caches are invalidated automatically.
    """
    from backend.services.post_service import _resolve_tags  # noqa: PLC0415

    if title is not None:
        post.title = title.strip()
    if markdown_body is not None:
        post.markdown_body = markdown_body
        post.reading_time_minutes = reading_time_minutes(markdown_body)
        invalidate_html_cache(post.slug)
    if seo_description is not None:
        post.seo_description = seo_description or None
    if tag_names is not None:
        post.tags = _resolve_tags(tag_names)

    post.updated_at = datetime.now(UTC)
    return post


# ── Revision helpers (workspace-scoped) ───────────────────────────────────────


def list_workspace_document_revisions(post: Post) -> list[Revision]:
    """Return all revisions for a workspace document, newest first."""
    return list(
        db.session.scalars(
            select(Revision)
            .where(Revision.post_id == post.id)
            .options(joinedload(Revision.author))
            .order_by(Revision.created_at.desc())
        )
    )


def list_workspace_document_release_notes(post: Post) -> list[PostReleaseNote]:
    """Return release notes (changelog) for a workspace document, newest first."""
    return list(
        db.session.scalars(
            select(PostReleaseNote)
            .where(PostReleaseNote.post_id == post.id)
            .order_by(PostReleaseNote.version_number.desc())
        )
    )


def list_workspace_document_versions(post: Post) -> list[PostVersion]:
    """Return all stored versions of a workspace document."""
    return list(
        db.session.scalars(
            select(PostVersion)
            .where(PostVersion.post_id == post.id)
            .order_by(PostVersion.version_number.desc())
        )
    )


# ── Clone to public ───────────────────────────────────────────────────────────


def _unique_public_slug(base: str) -> str:
    """Return *base* suffixed with -2, -3 … until it is unique among public posts.

    Only checks ``workspace_id IS NULL`` rows — workspace posts share a
    separate slug namespace (enforced by the partial unique index on the DB).
    """
    existing = set(
        db.session.scalars(
            select(Post.slug).where(
                Post.slug.like(f"{base}%"),
                Post.workspace_id.is_(None),
            )
        ).all()
    )
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


def clone_to_public(post: Post, cloner: User) -> Post:
    """Copy a workspace document to a brand-new *public* draft.

    The original workspace post is left unchanged.  The clone is created with
    ``workspace_id=NULL`` and ``status=draft`` — it is never auto-published.

    INV-001 is maintained because the clone is a draft; it will only enter the
    public published layer when the author explicitly publishes it.

    Raises
    ------
    ValueError
        If *post* is already on the public layer (``workspace_id is None``)
        or if *cloner* does not have at least editor-level access.

    Returns
    -------
    Post
        The newly created (flushed but not committed) public draft.
    """
    from backend.security.permissions import PermissionService  # noqa: PLC0415

    if post.workspace_id is None:
        raise ValueError("clone_to_public: post is already on the public layer")
    if not PermissionService.can_clone_to_public(cloner, post):
        raise PermissionError("clone_to_public: insufficient permission")

    base = re.sub(r"[^a-z0-9]+", "-", post.title.lower()).strip("-") or "untitled"
    slug = _unique_public_slug(base)

    clone = Post(
        workspace_id=None,
        status=PostStatus.draft,
        slug=slug,
        title=post.title,
        markdown_body=post.markdown_body,
        author_id=cloner.id,
        reading_time_minutes=post.reading_time_minutes,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    # Copy tags — relationship populated after flush.
    clone.tags = list(post.tags)

    db.session.add(clone)
    db.session.flush()  # Assign clone.id without committing.
    return clone
