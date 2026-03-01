"""Playbook service — template library + workspace playbook instance operations.

Template operations
-------------------
Templates are global (not workspace-scoped) and managed by editors/admins.
Each template can have multiple immutable versions (append-only).

Instance operations
-------------------
A playbook *instance* is a ``Post`` row with ``kind='playbook'`` and a
non-NULL ``workspace_id``.  All workspace membership checks are performed
by the caller (route layer) via
:func:`~backend.services.workspace_service.get_workspace_for_user` **before**
calling any function here.

Isolation guarantee
-------------------
Every instance query scopes to ``Post.workspace_id == workspace.id`` AND
``Post.kind == 'playbook'`` to prevent cross-workspace and cross-kind leakage.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.playbook import PlaybookTemplate, PlaybookTemplateVersion
from backend.models.post import Post, PostStatus
from backend.models.user import User
from backend.models.workspace import Workspace
from backend.utils.markdown import reading_time_minutes


# ── Slug helpers ──────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "playbook"


def _unique_playbook_slug(workspace_id: int, base: str) -> str:
    """Return a slug that is unique within (workspace_id, kind='playbook')."""
    candidate = base[:80]
    counter = 0
    while True:
        slug = candidate if counter == 0 else f"{candidate}-{counter}"
        exists = db.session.scalar(
            select(Post.id).where(
                Post.workspace_id == workspace_id,
                Post.kind == "playbook",
                Post.slug == slug,
            )
        )
        if exists is None:
            return slug
        counter += 1


# ── Template operations ───────────────────────────────────────────────────────


def list_templates(*, public_only: bool = True) -> list[PlaybookTemplate]:
    """Return all playbook templates, optionally filtered to public only."""
    stmt = select(PlaybookTemplate).order_by(PlaybookTemplate.name)
    if public_only:
        stmt = stmt.where(PlaybookTemplate.is_public.is_(True))
    return list(db.session.scalars(stmt))


def get_template_by_slug(slug: str) -> PlaybookTemplate | None:
    """Return the template with *slug*, eager-loading its versions."""
    return db.session.scalar(
        select(PlaybookTemplate)
        .where(PlaybookTemplate.slug == slug)
        .options(
            joinedload(PlaybookTemplate.versions),
        )
    )


def get_latest_template_version(
    template_id: int,
) -> PlaybookTemplateVersion | None:
    """Return the highest-numbered version for *template_id*."""
    return db.session.scalar(
        select(PlaybookTemplateVersion)
        .where(PlaybookTemplateVersion.template_id == template_id)
        .order_by(PlaybookTemplateVersion.version.desc())
        .limit(1)
    )


def get_template_version_by_id(
    version_id: int,
) -> PlaybookTemplateVersion | None:
    """Return a specific template version by PK."""
    return db.session.get(PlaybookTemplateVersion, version_id)


def create_template(
    *,
    name: str,
    slug: str,
    description: str | None = None,
    is_public: bool = True,
    created_by: User,
) -> PlaybookTemplate:
    """Create a new playbook template.  Caller must commit."""
    template = PlaybookTemplate(
        name=name.strip(),
        slug=slug.strip(),
        description=description,
        is_public=is_public,
        created_by_user_id=created_by.id,
    )
    db.session.add(template)
    return template


def create_template_version(
    *,
    template_id: int,
    schema_json: str | None = None,
    skeleton_md: str | None = None,
    change_notes: str | None = None,
    created_by: User,
) -> PlaybookTemplateVersion:
    """Append a new version to *template_id*.  Caller must commit.

    The version number is automatically set to ``max(existing) + 1`` (or 1).
    """
    current_max = db.session.scalar(
        select(PlaybookTemplateVersion.version)
        .where(PlaybookTemplateVersion.template_id == template_id)
        .order_by(PlaybookTemplateVersion.version.desc())
        .limit(1)
    )
    next_version = (current_max or 0) + 1

    tv = PlaybookTemplateVersion(
        template_id=template_id,
        version=next_version,
        schema_json=schema_json,
        skeleton_md=skeleton_md or "",
        change_notes=change_notes,
        created_by_user_id=created_by.id,
    )
    db.session.add(tv)
    return tv


# ── Instance (workspace playbook) operations ──────────────────────────────────


def list_workspace_playbooks(workspace: Workspace) -> list[Post]:
    """Return all playbook instances in *workspace*, newest-updated first.

    The caller MUST have already verified membership via
    :func:`~backend.services.workspace_service.get_workspace_for_user`.
    """
    from sqlalchemy.orm import selectinload  # noqa: PLC0415

    return list(
        db.session.scalars(
            select(Post)
            .where(
                Post.workspace_id == workspace.id,
                Post.kind == "playbook",
            )
            .options(joinedload(Post.author), selectinload(Post.tags))
            .order_by(Post.updated_at.desc())
        ).unique()
    )


def create_workspace_playbook(
    *,
    workspace: Workspace,
    creator: User,
    title: str,
    template_version_id: int | None = None,
    seo_description: str | None = None,
    slug: str | None = None,
) -> Post:
    """Create a new playbook instance inside *workspace*.

    If *template_version_id* is provided the playbook body is seeded from
    ``PlaybookTemplateVersion.skeleton_md``.  The ``template_id`` FK is
    resolved automatically from the version row.

    The post starts in ``draft`` status.  Caller must commit.
    The caller MUST hold at least the ``editor`` role.
    """
    markdown_body = ""
    resolved_template_id: int | None = None

    if template_version_id is not None:
        tv = get_template_version_by_id(template_version_id)
        if tv is not None:
            markdown_body = tv.skeleton_md or ""
            resolved_template_id = tv.template_id

    base = _slugify(slug or title)
    final_slug = _unique_playbook_slug(workspace.id, base)

    post = Post(
        title=title.strip(),
        slug=final_slug,
        kind="playbook",
        markdown_body=markdown_body,
        status=PostStatus.draft,
        author_id=creator.id,
        workspace_id=workspace.id,
        seo_description=seo_description,
        reading_time_minutes=reading_time_minutes(markdown_body),
        template_id=resolved_template_id,
        template_version_id=template_version_id,
    )
    db.session.add(post)
    return post


def get_workspace_playbook(
    workspace: Workspace,
    slug: str,
) -> Post | None:
    """Return the playbook with *slug* inside *workspace*, or ``None``.

    Intentionally scopes to ``kind='playbook'`` to prevent cross-kind slug
    collisions from leaking content.
    The caller MUST have already verified membership.
    """
    return db.session.scalar(
        select(Post)
        .where(
            Post.workspace_id == workspace.id,
            Post.kind == "playbook",
            Post.slug == slug,
        )
        .options(
            joinedload(Post.author),
            joinedload(Post.tags),
        )
    )
