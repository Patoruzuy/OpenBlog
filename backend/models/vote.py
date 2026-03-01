"""Vote model — upvotes on posts and comments.

target_type distinguishes what was voted on: 'post' or 'comment'.
A unique constraint on (user_id, target_type, target_id) prevents duplicate votes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Vote(db.Model):
    """A single upvote by a user on a post or comment."""

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 'post' or 'comment' — avoids a polymorphic join overhead for a simple table.
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint(
            "user_id", "target_type", "target_id", name="uq_votes_user_target"
        ),
        Index("ix_votes_target", "target_type", "target_id"),
    )

    def __repr__(self) -> str:
        return f"<Vote id={self.id} user_id={self.user_id} {self.target_type}:{self.target_id}>"
