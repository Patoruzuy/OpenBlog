"""Badge and UserBadge models — gamification layer.

Badge definitions are stored in the DB so admins can add new badges without
a code deploy.  The ``key`` field is a stable machine-readable identifier used
by BadgeService (Phase 6) to look up the correct badge to award.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Badge(db.Model):
    """A badge definition (awarded for specific contribution milestones)."""

    __tablename__ = "badges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Stable machine-readable key used by BadgeService, e.g. 'first_accepted_revision'
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    def __repr__(self) -> str:
        return f"<Badge key={self.key!r} name={self.name!r}>"


class UserBadge(db.Model):
    """An awarded badge instance — records when and why a user earned a badge."""

    __tablename__ = "user_badges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    badge_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("badges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="badges"
    )
    badge: Mapped[Badge] = relationship("Badge")

    __table_args__ = (
        UniqueConstraint("user_id", "badge_id", name="uq_user_badges_pair"),
    )

    def __repr__(self) -> str:
        return f"<UserBadge user_id={self.user_id} badge_id={self.badge_id}>"
