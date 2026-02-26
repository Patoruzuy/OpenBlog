"""Bookmark model — users saving posts to read later."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Bookmark(db.Model):
    """A post bookmarked by a user."""

    __tablename__ = "bookmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821
    post: Mapped[Post] = relationship("Post")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint("user_id", "post_id", name="uq_bookmarks_user_post"),
    )

    def __repr__(self) -> str:
        return f"<Bookmark user_id={self.user_id} post_id={self.post_id}>"
