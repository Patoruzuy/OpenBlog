"""Subscription model — normalized watch table (who watches what).

A subscription records that ``user_id`` wants to receive in-app (and
optionally email) notifications for all events on a given target.

Supported ``target_type`` values (MVP)
--------------------------------------
``post``        Public posts and workspace documents (both live in the posts
                table; workspace scope is implied by ``Post.workspace_id``).
``workspace``   Workspace-wide activity (any event on any doc within the ws).
``revision``    A specific revision proposal (rarely used, provided for
                completeness).
``user``        Activity by a specific user (follows).
``tag``         Posts tagged with a specific tag (future).

Permission rules
----------------
- ``post`` target where ``Post.workspace_id IS NOT NULL``:
    caller must be a workspace member.
- ``post`` target where ``Post.workspace_id IS NULL``:
    any authenticated user may subscribe; post must be published.
- ``workspace`` target:
    caller must be a workspace member.
- Other target types: any authenticated user.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Subscription(db.Model):
    """A user's watch subscription for a target entity."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="One of: workspace, post, revision, user, tag",
    )
    target_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint(
            "user_id", "target_type", "target_id", name="uq_subscriptions_user_target"
        ),
        CheckConstraint(
            "target_type IN ('workspace','post','revision','user','tag')",
            name="ck_subscriptions_target_type",
        ),
        Index("idx_subscriptions_target", "target_type", "target_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Subscription id={self.id} user_id={self.user_id} "
            f"{self.target_type}:{self.target_id}>"
        )
