"""Follow model — users following authors."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Follow(db.Model):
    """A directional follow: ``follower_id`` follows ``followed_id``."""

    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    follower_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    followed_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    follower: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[follower_id]
    )
    followed: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[followed_id]
    )

    __table_args__ = (
        UniqueConstraint("follower_id", "followed_id", name="uq_follows_pair"),
    )

    def __repr__(self) -> str:
        return f"<Follow follower={self.follower_id} → followed={self.followed_id}>"
