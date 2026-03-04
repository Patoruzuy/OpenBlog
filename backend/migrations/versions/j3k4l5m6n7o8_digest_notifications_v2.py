"""Notifications Phase 2: digest runs table, prefs extensions, beat schedule.

Revision ID: j3k4l5m6n7o8
Revises: i1j2k3l4m5n6
Create Date: 2026-03-02 00:00:00.000000

Schema changes
--------------
1.  Extend ``notification_preferences``:
      - last_digest_sent_at  TIMESTAMPTZ NULL        (dedup guard)
      - timezone              TEXT NOT NULL DEFAULT 'UTC'
      - digest_hour_local     SMALLINT NOT NULL DEFAULT 9

2.  Add ``digest_runs`` table — idempotency record per (user, frequency, period).
    Columns: id, user_id, frequency, period_key, period_start, period_end,
             notification_count, status, sent_at, error_message.
    Unique key: (user_id, frequency, period_key).

Reversible: downgrade() removes all additions cleanly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "j3k4l5m6n7o8"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None

_DIGEST_STATUSES = "('sent','skipped','failed')"
_FREQUENCIES = "('daily','weekly')"


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. Extend notification_preferences ───────────────────────────────
    op.add_column(
        "notification_preferences",
        sa.Column(
            "last_digest_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC timestamp of the last successful digest email sent to this user.",
        ),
    )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "timezone",
            sa.Text,
            nullable=False,
            server_default="UTC",
            comment="IANA timezone for scheduling digest sends (optional, informational).",
        ),
    )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "digest_hour_local",
            sa.SmallInteger,
            nullable=False,
            server_default="9",
            comment="Preferred local hour (0–23) for digest delivery.",
        ),
    )

    # ── 2. digest_runs ────────────────────────────────────────────────────
    op.create_table(
        "digest_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "frequency",
            sa.Text,
            nullable=False,
            comment="'daily' or 'weekly'",
        ),
        sa.Column(
            "period_key",
            sa.Text,
            nullable=False,
            comment="Stable dedup key: '2026-03-02' (daily) or '2026-W10' (weekly).",
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "notification_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="Number of notifications included in this digest.",
        ),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            comment="'sent', 'skipped', or 'failed'",
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.CheckConstraint(
            f"frequency IN {_FREQUENCIES}", name="ck_digest_runs_frequency"
        ),
        sa.CheckConstraint(
            f"status IN {_DIGEST_STATUSES}", name="ck_digest_runs_status"
        ),
        sa.UniqueConstraint(
            "user_id", "frequency", "period_key", name="uq_digest_runs_period"
        ),
    )
    op.create_index(
        "idx_digest_runs_user_freq",
        "digest_runs",
        ["user_id", "frequency", sa.text("period_start DESC")],
    )


# ── downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.drop_table("digest_runs")
    op.drop_column("notification_preferences", "digest_hour_local")
    op.drop_column("notification_preferences", "timezone")
    op.drop_column("notification_preferences", "last_digest_sent_at")
