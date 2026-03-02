"""Add content_links table for Knowledge Graph.

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-03-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "from_post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("link_type", sa.String(32), nullable=False),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "link_type IN ('related','derived_from','implements','supersedes','inspired_by','used_by')",
            name="ck_content_links_link_type",
        ),
    )
    op.create_index("ix_content_links_from_post_id", "content_links", ["from_post_id"])
    op.create_index("ix_content_links_to_post_id", "content_links", ["to_post_id"])
    op.create_index("ix_content_links_workspace_id", "content_links", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_content_links_workspace_id", table_name="content_links")
    op.drop_index("ix_content_links_to_post_id", table_name="content_links")
    op.drop_index("ix_content_links_from_post_id", table_name="content_links")
    op.drop_table("content_links")
