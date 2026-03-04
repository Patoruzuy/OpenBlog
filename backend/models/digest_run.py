"""DigestRun model — idempotency record for email digest delivery.

One row per (user, frequency, period_key).  The unique constraint on
``(user_id, frequency, period_key)`` prevents duplicate digest sends even
if the Celery task is retried or accidentally enqueued twice.

Period key format
-----------------
``'daily'``   → ``'YYYY-MM-DD'``      e.g. ``'2026-03-02'``
``'weekly'``  → ``'YYYY-Www'``         e.g. ``'2026-W10'``

Status lifecycle
-----------------
``skipped``  No notifications in the period window.
``sent``     Email delivered successfully.
``failed``   Error during delivery; ``error_message`` carries details.
"""

from __future__ import annotations

from datetime import datetime

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


class DigestRun(db.Model):
    """Idempotency record for a single digest send attempt."""

    __tablename__ = "digest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    frequency: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="'daily' or 'weekly'",
    )
    period_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Stable dedup key: '2026-03-02' (daily) or '2026-W10' (weekly).",
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    notification_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Number of notifications included in this digest.",
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="'sent', 'skipped', or 'failed'",
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint(
            "user_id", "frequency", "period_key", name="uq_digest_runs_period"
        ),
        CheckConstraint(
            "frequency IN ('daily','weekly')", name="ck_digest_runs_frequency"
        ),
        CheckConstraint(
            "status IN ('sent','skipped','failed')", name="ck_digest_runs_status"
        ),
        Index("idx_digest_runs_user_freq", "user_id", "frequency"),
    )

    def __repr__(self) -> str:
        return (
            f"<DigestRun id={self.id} user_id={self.user_id} "
            f"freq={self.frequency!r} period={self.period_key!r} status={self.status!r}>"
        )
