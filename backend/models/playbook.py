"""Playbook template models.

PlaybookTemplate — a global template definition (slug, name, description,
                   is_public flag).  Templates are NOT workspace-scoped; they
                   describe *how* a playbook should be structured.

PlaybookTemplateVersion — an immutable snapshot of a template at a point in
                          time.  Each version carries a ``skeleton_md`` body
                          (pre-populated Markdown) and an optional
                          ``schema_json`` payload for future structured-field
                          support.  Versions are append-only; no updates.

Playbook instances are ordinary ``Post`` rows with ``kind='playbook'`` and a
non-NULL ``workspace_id``.  They may optionally reference the template version
they were seeded from via ``Post.template_version_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class PlaybookTemplate(db.Model):
    """Global playbook template (not workspace-scoped)."""

    __tablename__ = "playbook_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    versions: Mapped[list[PlaybookTemplateVersion]] = relationship(
        "PlaybookTemplateVersion",
        back_populates="template",
        lazy="select",
        order_by="PlaybookTemplateVersion.version",
        cascade="all, delete-orphan",
    )
    created_by: Mapped[object | None] = relationship(
        "User",
        foreign_keys="PlaybookTemplate.created_by_user_id",
        lazy="select",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (Index("ix_playbook_templates_public", "is_public"),)

    def __repr__(self) -> str:
        return f"<PlaybookTemplate id={self.id} slug={self.slug!r}>"

    @property
    def latest_version(self) -> PlaybookTemplateVersion | None:
        """Return the highest-numbered version, or None if no versions exist."""
        if not self.versions:
            return None
        return max(self.versions, key=lambda v: v.version)


class PlaybookTemplateVersion(db.Model):
    """Immutable snapshot of a PlaybookTemplate at a specific version number."""

    __tablename__ = "playbook_template_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("playbook_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # JSON string describing structured fields (reserved for future use).
    schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pre-populated Markdown body seeded into new playbook instances.
    skeleton_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    template: Mapped[PlaybookTemplate] = relationship(
        "PlaybookTemplate", back_populates="versions"
    )
    created_by: Mapped[object | None] = relationship(
        "User",
        foreign_keys="PlaybookTemplateVersion.created_by_user_id",
        lazy="select",
    )

    # ── Constraints & indexes ─────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_ptv_template_version"),
        Index("ix_playbook_template_versions_template", "template_id", "version"),
    )

    def __repr__(self) -> str:
        return (
            f"<PlaybookTemplateVersion template_id={self.template_id} v{self.version}>"
        )
