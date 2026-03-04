"""ReputationEvent model — append-only reputation ledger.

Each row represents a single, immutable reputation-change event.
The ``fingerprint`` column (UNIQUE) is the idempotency guard:
attempting to insert a duplicate fingerprint raises IntegrityError,
which is caught by ReputationService.award_event() and treated as a
no-op rather than a double-award.

scope
-----
workspace_id IS NULL  → public event (contributes to public total and
                        User.reputation_score)
workspace_id IS NOT NULL → workspace-scoped event (never exposed on
                           public routes)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.extensions import db


class ReputationEvent(db.Model):
    """A single immutable entry in the reputation audit ledger."""

    __tablename__ = "reputation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Scope ──────────────────────────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Classification ──────────────────────────────────────────────────────
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Idempotency key ─────────────────────────────────────────────────────
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # ── Payload ─────────────────────────────────────────────────────────────
    # Stored as JSON text for SQLite (test) + PostgreSQL (production) compat.
    _metadata_json: Mapped[str] = mapped_column(
        "metadata_json",
        Text,
        nullable=False,
        default="{}",
    )

    # ── Timestamp ───────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Table-level constraints & indexes ───────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('revision_accepted','revision_rejected',"
            "'vote_received','ab_win','admin_adjustment')",
            name="ck_reputation_events_event_type",
        ),
        CheckConstraint(
            "source_type IN ('revision','post','prompt','ab_experiment','vote')",
            name="ck_reputation_events_source_type",
        ),
        CheckConstraint(
            "points BETWEEN -500 AND 500",
            name="ck_reputation_events_points",
        ),
        # Composite index for per-user per-scope listing with recency sort.
        Index("ix_rep_events_user_ws_date", "user_id", "workspace_id", "created_at"),
        # Index for workspace-level aggregation.
        Index("ix_rep_events_ws_date", "workspace_id", "created_at"),
    )

    # ── Convenience accessor ─────────────────────────────────────────────────

    @property
    def metadata_dict(self) -> dict:
        """Deserialise the stored JSON metadata blob."""
        try:
            return json.loads(self._metadata_json or "{}")
        except (ValueError, TypeError):
            return {}

    def __repr__(self) -> str:
        scope = f"ws={self.workspace_id}" if self.workspace_id is not None else "public"
        return (
            f"<ReputationEvent id={self.id} user_id={self.user_id} "
            f"{scope} {self.event_type!r} {self.points:+d}>"
        )
