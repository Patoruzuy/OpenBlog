"""Comment model — threaded discussions on posts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Comment(db.Model):
    """A comment on a post.  Supports one level of threading via parent_id."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable self-FK for threading; top-level comments have parent_id=None.
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )

    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Soft-delete: preserve thread structure, body replaced with tombstone text."
    )
    is_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Moderation flag: hidden from public until reviewed."
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

    # ── Contribution identity snapshot ──────────────────────────
    public_identity_mode: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
        comment="Identity mode at post time: public|pseudonymous|anonymous"
    )
    public_display_name_snapshot: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
        comment="Display name or pseudonym captured at post time."
    )
    public_avatar_snapshot: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
        comment="Avatar URL captured at post time (may be None for anonymous)."
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped[Post] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Post",
        back_populates="comments",
        foreign_keys=[post_id],
    )
    author: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="comments"
    )
    replies: Mapped[list[Comment]] = relationship(
        "Comment",
        back_populates="parent",
        lazy="select",
        cascade="all, delete-orphan",
    )
    parent: Mapped[Comment | None] = relationship(
        "Comment", back_populates="replies", remote_side=[id]
    )

    __table_args__ = (
        Index("ix_comments_post_parent", "post_id", "parent_id"),
    )

    def __repr__(self) -> str:
        return f"<Comment id={self.id} post_id={self.post_id} author_id={self.author_id}>"
