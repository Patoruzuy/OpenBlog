"""Notification model — in-app alerts delivered to users.

Notification types are kept as a string enum so new types can be added
without an Alembic migration (ALTER TYPE on PostgreSQL enums is painful).
Each notification carries an optional ``payload`` JSON string for
type-specific rendering data (e.g. the slug of the accepted post).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Notification(db.Model):
    """An in-app notification for a user."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Free-form type key, e.g. 'revision_accepted', 'comment_reply', 'new_follower'
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON string carrying type-specific context (e.g. {"post_slug": "my-post"})
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="notifications"
    )

    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "is_read"),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} user_id={self.user_id} "
            f"type={self.notification_type!r} read={self.is_read}>"
        )
