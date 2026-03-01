"""Add user_post_reads table.

Tracks which version of each post an authenticated user last read.
Used to show "Updated since your last visit" indicators.

Revision ID: b1d4f6c8e295
Revises: a8e2f4c6d031
Create Date: 2026-02-28 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1d4f6c8e295"
down_revision = "a8e2f4c6d031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_post_reads",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_read_version",
            sa.Integer,
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_user_post_reads_user_id", "user_post_reads", ["user_id"])
    op.create_index("ix_user_post_reads_post_id", "user_post_reads", ["post_id"])
    op.create_unique_constraint(
        "uq_user_post_reads_user_post",
        "user_post_reads",
        ["user_id", "post_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_user_post_reads_user_post", "user_post_reads", type_="unique"
    )
    op.drop_index("ix_user_post_reads_post_id", table_name="user_post_reads")
    op.drop_index("ix_user_post_reads_user_id", table_name="user_post_reads")
    op.drop_table("user_post_reads")
