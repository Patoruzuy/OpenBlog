"""ContentLink model — structured relationships between posts/prompts/playbooks.

Design
------
Every relation is a directed edge:  from_post → to_post  labelled by link_type.

Workspace isolation
-------------------
``workspace_id IS NULL``  → public-layer link.  Both posts must be public.
``workspace_id = ws.id``  → workspace-layer link.  from_post must belong to
    that workspace; to_post may be public OR in the same workspace.

The UNIQUE constraint is on (from_post_id, to_post_id, link_type, workspace_id).
NULL workspace_id participates in the unique constraint via the database NULL
semantics workaround handled at the service layer (checking before insert).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db

VALID_LINK_TYPES: tuple[str, ...] = (
    "related",
    "derived_from",
    "implements",
    "supersedes",
    "inspired_by",
    "used_by",
)


class ContentLink(db.Model):
    """A directed, typed relationship between two Post rows."""

    __tablename__ = "content_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    from_post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    link_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # NULL → public-layer link; NOT NULL → workspace-layer link.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )

    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    from_post: Mapped[object] = relationship(
        "Post",
        foreign_keys=[from_post_id],
        lazy="select",
    )
    to_post: Mapped[object] = relationship(
        "Post",
        foreign_keys=[to_post_id],
        lazy="select",
    )
    created_by: Mapped[object | None] = relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="select",
    )

    __table_args__ = (
        CheckConstraint(
            f"link_type IN ({', '.join(repr(t) for t in VALID_LINK_TYPES)})",
            name="ck_content_links_link_type",
        ),
        # NOTE: UNIQUE on nullable workspace_id is enforced at the service layer
        # (duplicate check before insert) for full SQLite + PostgreSQL compat.
        Index("ix_content_links_from_post_id", "from_post_id"),
        Index("ix_content_links_to_post_id", "to_post_id"),
        Index("ix_content_links_workspace_id", "workspace_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<ContentLink id={self.id} "
            f"{self.from_post_id}→{self.to_post_id} [{self.link_type}]>"
        )
