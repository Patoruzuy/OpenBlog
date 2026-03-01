"""attachments storage + newsletter + email delivery log

Revision ID: b2c4e6f8a0d1
Revises: a1b3c5d7e9f2
Create Date: 2026-02-27 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b2c4e6f8a0d1"
down_revision = "a1b3c5d7e9f2"
branch_labels = None
depends_on = None


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ── 1. comment_attachments: add real-storage columns, rename file_size → size_bytes ──
    with op.batch_alter_table("comment_attachments") as batch_op:
        # Rename existing columns to new names
        batch_op.alter_column("filename", new_column_name="original_filename")
        batch_op.alter_column("file_size", new_column_name="size_bytes")
        # Add new columns
        batch_op.add_column(sa.Column("stored_path", sa.String(500), nullable=True))
        batch_op.add_column(sa.Column("sha256", sa.String(64), nullable=True))
        batch_op.add_column(
            sa.Column("is_image", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )

    # ── 2. newsletter_subscriptions ────────────────────────────────────────
    op.create_table(
        "newsletter_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_token_hash", sa.String(64), nullable=True),
        sa.Column("confirm_token_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribe_token_hash", sa.String(64), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("locale", sa.String(10), nullable=False, server_default="en"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_newsletter_subscriptions_email",
        "newsletter_subscriptions",
        ["email"],
        unique=True,
    )
    op.create_index(
        "ix_newsletter_subscriptions_status", "newsletter_subscriptions", ["status"]
    )
    op.create_index(
        "ix_newsletter_subscriptions_user_id", "newsletter_subscriptions", ["user_id"]
    )

    # ── 3. email_delivery_logs ─────────────────────────────────────────────
    op.create_table(
        "email_delivery_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("to_email", sa.String(254), nullable=False),
        sa.Column("template_key", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_delivery_logs_to_email", "email_delivery_logs", ["to_email"]
    )
    op.create_index("ix_email_delivery_logs_status", "email_delivery_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_email_delivery_logs_status", table_name="email_delivery_logs")
    op.drop_index("ix_email_delivery_logs_to_email", table_name="email_delivery_logs")
    op.drop_table("email_delivery_logs")

    op.drop_index(
        "ix_newsletter_subscriptions_user_id", table_name="newsletter_subscriptions"
    )
    op.drop_index(
        "ix_newsletter_subscriptions_status", table_name="newsletter_subscriptions"
    )
    op.drop_index(
        "ix_newsletter_subscriptions_email", table_name="newsletter_subscriptions"
    )
    op.drop_table("newsletter_subscriptions")

    with op.batch_alter_table("comment_attachments") as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("is_image")
        batch_op.drop_column("sha256")
        batch_op.drop_column("stored_path")
        batch_op.alter_column("size_bytes", new_column_name="file_size")
        batch_op.alter_column("original_filename", new_column_name="filename")
