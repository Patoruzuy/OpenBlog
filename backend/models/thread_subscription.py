"""ThreadSubscription model — users following a post's comment thread."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class ThreadSubscription(db.Model):
    """Records that a user wants email/notification updates for a post thread."""

    __tablename__ = "thread_subscriptions"

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
        UniqueConstraint(
            "user_id", "post_id", name="uq_thread_subscriptions_user_post"
        ),
    )
