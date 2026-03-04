"""Reputation system: append-only ledger and aggregate cache.

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-03-04 00:00:00.000000

Schema changes
--------------
1. Create table ``reputation_events``
   - Append-only audit ledger; unique fingerprint prevents double-awards.
   - CHECK constraints on event_type, source_type, points range.
2. Create table ``reputation_totals``
   - Aggregate cache keyed by (user_id, workspace_id scope).
   - Uses partial unique indexes (not a nullable composite PK) so that
     both public (workspace_id IS NULL) and workspace rows are safe.
3. No change to the ``users`` table.

Reversible: down() drops both tables in reverse-dependency order.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q5r6s7t8u9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── reputation_events ─────────────────────────────────────────────────────
    op.create_table(
        "reputation_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "event_type",
            sa.Text,
            sa.CheckConstraint(
                "event_type IN ('revision_accepted','revision_rejected',"
                "'vote_received','ab_win','admin_adjustment')",
                name="ck_reputation_events_event_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "source_type",
            sa.Text,
            sa.CheckConstraint(
                "source_type IN ('revision','post','prompt','ab_experiment','vote')",
                name="ck_reputation_events_source_type",
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.Integer, nullable=False),
        sa.Column(
            "points",
            sa.Integer,
            sa.CheckConstraint(
                "points BETWEEN -500 AND 500",
                name="ck_reputation_events_points",
            ),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.Text, nullable=False),
        # Text column stores JSON; avoids JSONB dialect dependency in tests.
        sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Unique index on fingerprint — primary idempotency guard.
    op.create_index(
        "uq_reputation_events_fingerprint",
        "reputation_events",
        ["fingerprint"],
        unique=True,
    )
    # Composite index for per-user-per-scope event listing with date sort.
    op.create_index(
        "ix_rep_events_user_ws_date",
        "reputation_events",
        ["user_id", "workspace_id", "created_at"],
    )
    # Composite index for per-workspace aggregation.
    op.create_index(
        "ix_rep_events_ws_date",
        "reputation_events",
        ["workspace_id", "created_at"],
    )

    # ── reputation_totals ─────────────────────────────────────────────────────
    op.create_table(
        "reputation_totals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "points_total",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Partial unique indexes enforce one-row-per-scope without a nullable PK.
    # Both PostgreSQL and SQLite 3.8+ support WHERE in CREATE UNIQUE INDEX.
    op.create_index(
        "uq_rep_totals_public_user",
        "reputation_totals",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
        sqlite_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index(
        "uq_rep_totals_ws_user",
        "reputation_totals",
        ["user_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
        sqlite_where=sa.text("workspace_id IS NOT NULL"),
    )
    # Index for leaderboard queries: rank users within a workspace by score.
    op.create_index(
        "ix_rep_totals_ws_points",
        "reputation_totals",
        ["workspace_id", "points_total"],
    )


# ── downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.drop_index("ix_rep_totals_ws_points", table_name="reputation_totals")
    op.drop_index("uq_rep_totals_ws_user", table_name="reputation_totals")
    op.drop_index("uq_rep_totals_public_user", table_name="reputation_totals")
    op.drop_table("reputation_totals")

    op.drop_index("ix_rep_events_ws_date", table_name="reputation_events")
    op.drop_index("ix_rep_events_user_ws_date", table_name="reputation_events")
    op.drop_index("uq_reputation_events_fingerprint", table_name="reputation_events")
    op.drop_table("reputation_events")
