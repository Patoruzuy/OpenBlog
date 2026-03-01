"""Post model.

Post statuses
-------------
draft      — not visible publicly; only author and editors can view
published  — live and publicly accessible
scheduled  — will be published at ``publish_at`` by a Celery beat task
archived   — hidden from listing; accessible by direct URL

Full-text search
----------------
``search_vector`` is a PostgreSQL ``tsvector`` column updated by a DB trigger
(added in the Alembic migration).  A GIN index enables fast ``@@`` queries.
On SQLite (unit tests) this column is absent — the model degrades gracefully
because it is defined as server-side only.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class PostStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    scheduled = "scheduled"
    archived = "archived"


class Post(db.Model):
    """A blog post.  The canonical content is ``markdown_body`` (DB-authoritative)."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Content ────────────────────────────────────────────────────────────
    slug: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    markdown_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # rendered_html is NOT persisted; it is cached in Redis and re-derived on update.

    # ── Status & versioning ────────────────────────────────────────────────
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus, name="post_status"),
        nullable=False,
        default=PostStatus.draft,
        server_default=PostStatus.draft.value,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="Monotonically increasing; bumped on every accepted revision.",
    )
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Scheduling ─────────────────────────────────────────────────────────
    publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Metrics ────────────────────────────────────────────────────────────
    reading_time_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── SEO ────────────────────────────────────────────────────────────────
    seo_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    seo_description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    og_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── Authorship ─────────────────────────────────────────────────────────
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Workspace scope ────────────────────────────────────────────────────
    # NULL → public layer (visible to everyone).
    # NOT NULL → workspace layer (visible only to workspace members).
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    # ── Content kind ───────────────────────────────────────────────────────
    # 'article'   — regular public blog post (default for all existing rows)
    # 'playbook'  — workspace-scoped playbook instance
    # 'prompt'    — workspace-scoped prompt document (reserved)
    # 'framework' — workspace-scoped framework document (reserved)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="article", server_default="article"
    )

    # ── Template reference ─────────────────────────────────────────────────
    # Set when a playbook was seeded from a PlaybookTemplateVersion.
    template_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("playbook_templates.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    template_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("playbook_template_versions.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    # ── Autosave ───────────────────────────────────────────────────────────
    last_autosaved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Set by the autosave endpoint; NULL until first autosave.",
    )
    autosave_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Optimistic concurrency token; incremented on each autosave write.",
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────────
    author: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="posts"
    )
    workspace: Mapped[Workspace | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Workspace",
        foreign_keys="Post.workspace_id",
    )
    template: Mapped[object | None] = relationship(
        "PlaybookTemplate",
        foreign_keys="Post.template_id",
        lazy="select",
    )
    template_version: Mapped[object | None] = relationship(
        "PlaybookTemplateVersion",
        foreign_keys="Post.template_version_id",
        lazy="select",
    )
    tags: Mapped[list[Tag]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Tag", secondary="post_tags", back_populates="posts", lazy="select"
    )
    versions: Mapped[list[PostVersion]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "PostVersion",
        back_populates="post",
        lazy="select",
        order_by="PostVersion.version_number",
    )
    revisions: Mapped[list[Revision]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Revision", back_populates="post", lazy="select"
    )
    release_notes: Mapped[list[PostReleaseNote]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "PostReleaseNote",
        back_populates="post",
        lazy="select",
        order_by="PostReleaseNote.version_number.desc()",
    )
    comments: Mapped[list[Comment]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Comment",
        back_populates="post",
        lazy="select",
        primaryjoin="and_(Comment.post_id == Post.id, Comment.parent_id == None)",  # noqa: E711
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    # The tsvector column + GIN index are created in the Alembic migration via
    # raw SQL (op.execute) because SQLAlchemy doesn't model tsvector natively.
    # They are NOT declared here to keep the model SQLite-compatible for tests.
    __table_args__ = (
        # Partial unique indexes enforce slug uniqueness scoped by workspace.
        # Public posts (workspace_id IS NULL): slug must be unique globally.
        Index(
            "uq_posts_public_slug",
            "slug",
            unique=True,
            sqlite_where=text("workspace_id IS NULL"),
            postgresql_where=text("workspace_id IS NULL"),
        ),
        # Workspace posts: (workspace_id, slug) pair must be unique per workspace.
        Index(
            "uq_posts_workspace_slug",
            "workspace_id",
            "slug",
            unique=True,
            sqlite_where=text("workspace_id IS NOT NULL"),
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        Index("ix_posts_status_published_at", "status", "published_at"),
        Index("ix_posts_workspace_id", "workspace_id"),
        Index("ix_posts_kind", "kind"),
        Index("ix_posts_workspace_kind", "workspace_id", "kind"),
    )

    def __repr__(self) -> str:
        return f"<Post id={self.id} slug={self.slug!r} status={self.status.value!r}>"
