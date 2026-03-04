"""Notifications MVP: subscriptions, preferences, and structured notification columns.

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-03-02 00:00:00.000000

Schema changes
--------------
1.  Add ``notification_preferences`` table (one row per user).
2.  Add ``subscriptions`` table (watch-list: who watches what).
3.  Extend ``notifications`` with structured event columns:
      actor_user_id, event_type, target_type, target_id, payload_json, fingerprint.
4.  Add compound indexes on notifications and subscriptions for query performance.

Reversible: downgrade() drops all additions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i1j2k3l4m5n6"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


_VALID_TARGET_TYPES = "('workspace','post','revision','user','tag')"
_VALID_DIGEST_FREQS = "('none','daily','weekly')"


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. notification_preferences ───────────────────────────────────────
    op.create_table(
        "notification_preferences",
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "in_app_enabled", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "email_enabled", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "email_digest_frequency", sa.Text, nullable=False, server_default="none"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"email_digest_frequency IN {_VALID_DIGEST_FREQS}",
            name="ck_notif_prefs_digest_freq",
        ),
    )

    # ── 2. subscriptions ──────────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", sa.BigInteger, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"target_type IN {_VALID_TARGET_TYPES}",
            name="ck_subscriptions_target_type",
        ),
        sa.UniqueConstraint(
            "user_id", "target_type", "target_id", name="uq_subscriptions_user_target"
        ),
    )
    op.create_index(
        "idx_subscriptions_target", "subscriptions", ["target_type", "target_id"]
    )
    op.create_index(
        "idx_subscriptions_user",
        "subscriptions",
        ["user_id", sa.text("created_at DESC")],
    )

    # ── 3. Extend notifications ────────────────────────────────────────────
    op.add_column(
        "notifications",
        sa.Column(
            "actor_user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("notifications", sa.Column("event_type", sa.Text, nullable=True))
    op.add_column("notifications", sa.Column("target_type", sa.Text, nullable=True))
    op.add_column("notifications", sa.Column("target_id", sa.BigInteger, nullable=True))
    op.add_column("notifications", sa.Column("payload_json", sa.JSON, nullable=True))
    op.add_column("notifications", sa.Column("fingerprint", sa.Text, nullable=True))

    # Dedup index: only one notification per (user, fingerprint) when fingerprint set.
    op.create_index(
        "uq_notifications_fingerprint",
        "notifications",
        ["user_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )
    op.create_index(
        "idx_notifications_user_unread",
        "notifications",
        ["user_id", "is_read", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_notifications_target",
        "notifications",
        ["target_type", "target_id", sa.text("created_at DESC")],
    )


# ── downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    # Reverse order of upgrade.
    op.drop_index("idx_notifications_target", table_name="notifications")
    op.drop_index("idx_notifications_user_unread", table_name="notifications")
    op.drop_index("uq_notifications_fingerprint", table_name="notifications")

    op.drop_column("notifications", "fingerprint")
    op.drop_column("notifications", "payload_json")
    op.drop_column("notifications", "target_id")
    op.drop_column("notifications", "target_type")
    op.drop_column("notifications", "event_type")
    op.drop_column("notifications", "actor_user_id")

    op.drop_index("idx_subscriptions_user", table_name="subscriptions")
    op.drop_index("idx_subscriptions_target", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("notification_preferences")
