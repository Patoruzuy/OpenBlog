"""Tag model and Post↔Tag association table."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db

# ── Association table (no ORM class needed) ────────────────────────────────────
PostTag = Table(
    "post_tags",
    db.metadata,
    Column("post_id", Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(db.Model):
    """A topic tag that can be applied to many posts."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    color: Mapped[str | None] = mapped_column(
        String(7), nullable=True, comment="Hex color code, e.g. '#58a6ff'"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    posts: Mapped[list] = relationship(
        "Post", secondary="post_tags", back_populates="tags", lazy="select"
    )

    __table_args__ = (UniqueConstraint("slug", name="uq_tags_slug"),)

    def __repr__(self) -> str:
        return f"<Tag id={self.id} name={self.name!r}>"
