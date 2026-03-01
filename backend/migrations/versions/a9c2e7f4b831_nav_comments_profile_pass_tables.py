"""add reports, thread_subscriptions, pinned_posts, comment_attachments

Revision ID: a9c2e7f4b831
Revises: f3a7d9e2b451
Create Date: 2025-01-01 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "a9c2e7f4b831"
down_revision = "f3a7d9e2b451"
branch_labels = None
depends_on = None


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ── reports ──────────────────────────────────────────────────────────────
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reporter_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_target", "reports", ["target_type", "target_id"])
    op.create_index("ix_reports_status", "reports", ["status"])

    # ── thread_subscriptions ──────────────────────────────────────────────────
    op.create_table(
        "thread_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "post_id", name="uq_thread_subscriptions_user_post"
        ),
    )
    op.create_index(
        "ix_thread_subscriptions_user_id", "thread_subscriptions", ["user_id"]
    )
    op.create_index(
        "ix_thread_subscriptions_post_id", "thread_subscriptions", ["post_id"]
    )

    # ── pinned_posts ──────────────────────────────────────────────────────────
    op.create_table(
        "pinned_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "post_id", name="uq_pinned_posts_user_post"),
    )
    op.create_index("ix_pinned_posts_user_id", "pinned_posts", ["user_id"])

    # ── comment_attachments ───────────────────────────────────────────────────
    op.create_table(
        "comment_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("comment_id", sa.Integer(), nullable=True),
        sa.Column("uploader_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column(
            "storage_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_comment_attachments_comment_id",
        "comment_attachments",
        ["comment_id"],
    )
    op.create_index(
        "ix_comment_attachments_uploader_id",
        "comment_attachments",
        ["uploader_id"],
    )


def downgrade() -> None:
    op.drop_table("comment_attachments")
    op.drop_table("pinned_posts")
    op.drop_table("thread_subscriptions")
    op.drop_table("reports")
