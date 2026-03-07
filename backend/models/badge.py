"""Badge and UserBadge models — gamification layer.

Badge definitions are stored in the DB so admins can add new badges without
a code deploy.  The ``key`` field is a stable machine-readable identifier used
by BadgeService to look up the correct badge to award.

Scope rules
-----------
UserBadge.workspace_id IS NULL  → public badge (visible on public profile)
UserBadge.workspace_id IS NOT NULL → workspace-scoped badge (never public)

Uniqueness is enforced by two PARTIAL unique indexes (not a single constraint):
  uq_user_badges_public ON (user_id, badge_id) WHERE workspace_id IS NULL
  uq_user_badges_ws     ON (user_id, badge_id, workspace_id)
                           WHERE workspace_id IS NOT NULL
This means a user can hold the same badge in multiple workspaces but never
the same badge twice in the same scope.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Badge(db.Model):
    """A badge definition (awarded for specific contribution milestones)."""

    __tablename__ = "badges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Stable machine-readable key used by BadgeService, e.g. 'first_accepted_revision'
    key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # v1 additions
    category: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="contribution"
    )
    threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)

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

    # Scope — NULL means public badge, non-NULL = workspace-scoped badge.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="badges"
    )
    badge: Mapped[Badge] = relationship("Badge")

    __table_args__ = (
        # Partial unique: one public badge per user per badge type.
        Index(
            "uq_user_badges_public",
            "user_id",
            "badge_id",
            unique=True,
            postgresql_where=sa_text("workspace_id IS NULL"),
            sqlite_where=sa_text("workspace_id IS NULL"),
        ),
        # Partial unique: one workspace badge per (user, badge, workspace).
        Index(
            "uq_user_badges_ws",
            "user_id",
            "badge_id",
            "workspace_id",
            unique=True,
            postgresql_where=sa_text("workspace_id IS NOT NULL"),
            sqlite_where=sa_text("workspace_id IS NOT NULL"),
        ),
        # Performance index: list badges newest-first.
        Index("ix_user_badges_user_awarded", "user_id", "awarded_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<UserBadge user_id={self.user_id} badge_id={self.badge_id}"
            f" ws={self.workspace_id}>"
        )
