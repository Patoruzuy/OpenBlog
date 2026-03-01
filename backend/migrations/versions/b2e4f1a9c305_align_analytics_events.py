"""Align analytics_events to Phase-11 AnalyticsEvent model.

Revision ID: b2e4f1a9c305
Revises: 7cc9a6ad73f6
Create Date: 2026-02-25 00:00:00.000000

Changes
-------
The initial migration created analytics_events with a placeholder schema
(ip_address, user_agent, path, extra, created_at).  Phase 11 finalised the
model to store only anonymised data:

  - DROP   ip_address    (PII — never stored)
  - DROP   user_agent    (raw UA string — privacy)
  - DROP   path          (unused)
  - DROP   extra         (unused JSON blob)
  - ADD    user_agent_hash  VARCHAR(64)  — SHA-256[:16] prefix of UA
  - ADD    country_code     VARCHAR(2)   — optional geo code
  - RENAME created_at → occurred_at  (clearer semantics for event time)
  - DROP   old indexes (event_type, post_id, created_at)
  - ADD    composite index  (post_id, event_type, occurred_at)
  - ADD    composite index  (event_type, occurred_at)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e4f1a9c305"
down_revision: str | Sequence[str] | None = "7cc9a6ad73f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name  # "postgresql" or "sqlite"

    # ── Drop stale columns ─────────────────────────────────────────────────
    # SQLite does not support DROP COLUMN before 3.35; we use batch_alter_table
    # which recreates the table on SQLite and uses native ALTER on PostgreSQL.
    with op.batch_alter_table("analytics_events") as batch_op:
        batch_op.drop_column("ip_address")
        batch_op.drop_column("user_agent")
        batch_op.drop_column("path")
        batch_op.drop_column("extra")

    # ── Rename created_at → occurred_at ───────────────────────────────────
    with op.batch_alter_table("analytics_events") as batch_op:
        batch_op.alter_column(
            "created_at",
            new_column_name="occurred_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            existing_server_default=sa.func.now(),
        )

    # ── Add new columns ────────────────────────────────────────────────────
    with op.batch_alter_table("analytics_events") as batch_op:
        batch_op.add_column(sa.Column("user_agent_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("country_code", sa.String(2), nullable=True))

    # ── Drop old indexes ───────────────────────────────────────────────────
    # These were created by the initial migration; drop before recreating.
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_analytics_events_event_type")
        op.execute("DROP INDEX IF EXISTS ix_analytics_events_post_id")
        op.execute("DROP INDEX IF EXISTS ix_analytics_events_created_at")
    else:
        # SQLite batch rebuild above already discards the old indexes.
        # Attempt a graceful drop in case they survived.
        with op.batch_alter_table("analytics_events") as batch_op:
            for idx in (
                "ix_analytics_events_event_type",
                "ix_analytics_events_post_id",
                "ix_analytics_events_created_at",
            ):
                try:
                    batch_op.drop_index(idx)
                except Exception:
                    pass

    # ── Create new composite indexes ───────────────────────────────────────
    op.create_index(
        "ix_analytics_post_type_time",
        "analytics_events",
        ["post_id", "event_type", "occurred_at"],
    )
    op.create_index(
        "ix_analytics_event_time",
        "analytics_events",
        ["event_type", "occurred_at"],
    )


def downgrade() -> None:
    # ── Drop new indexes ───────────────────────────────────────────────────
    op.drop_index("ix_analytics_event_time", table_name="analytics_events")
    op.drop_index("ix_analytics_post_type_time", table_name="analytics_events")

    # ── Remove new columns ─────────────────────────────────────────────────
    with op.batch_alter_table("analytics_events") as batch_op:
        batch_op.drop_column("country_code")
        batch_op.drop_column("user_agent_hash")

    # ── Rename occurred_at → created_at ───────────────────────────────────
    with op.batch_alter_table("analytics_events") as batch_op:
        batch_op.alter_column(
            "occurred_at",
            new_column_name="created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )

    # ── Restore stale columns ──────────────────────────────────────────────
    with op.batch_alter_table("analytics_events") as batch_op:
        batch_op.add_column(sa.Column("ip_address", sa.String(45), nullable=True))
        batch_op.add_column(sa.Column("user_agent", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("path", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("extra", sa.JSON, nullable=True))

    # ── Restore old indexes ────────────────────────────────────────────────
    op.create_index(
        "ix_analytics_events_event_type", "analytics_events", ["event_type"]
    )
    op.create_index("ix_analytics_events_post_id", "analytics_events", ["post_id"])
    op.create_index(
        "ix_analytics_events_created_at", "analytics_events", ["created_at"]
    )
