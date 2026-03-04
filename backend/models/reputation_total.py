"""ReputationTotal model — cached aggregate of reputation points per scope.

Design note on uniqueness
-------------------------
Rather than a nullable composite primary key (which has ambiguous semantics
for NULL comparisons across databases), this table uses a regular ``id``
primary key and enforces uniqueness via *partial* unique indexes:

  uq_rep_totals_public_user   UNIQUE (user_id) WHERE workspace_id IS NULL
  uq_rep_totals_ws_user       UNIQUE (user_id, workspace_id)
                              WHERE workspace_id IS NOT NULL

This guarantees exactly one public row and one row per workspace per user,
while keeping NULL semantics unambiguous.

scope
-----
workspace_id IS NULL  → public total; mirrored into User.reputation_score
workspace_id IS NOT NULL → workspace-scoped total (never exposed publicly)
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.extensions import db


class ReputationTotal(db.Model):
    """Cached aggregate of a user's reputation within one scope."""

    __tablename__ = "reputation_totals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Scope key ───────────────────────────────────────────────────────────
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

    # ── Cache ───────────────────────────────────────────────────────────────
    points_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Index (partial unique indexes defined in migration) ─────────────────
    __table_args__ = (
        # For leaderboard: rank users within a workspace/public by total.
        Index("ix_rep_totals_ws_points", "workspace_id", "points_total"),
    )

    def __repr__(self) -> str:
        scope = f"ws={self.workspace_id}" if self.workspace_id is not None else "public"
        return (
            f"<ReputationTotal id={self.id} user_id={self.user_id} "
            f"{scope} pts={self.points_total}>"
        )
