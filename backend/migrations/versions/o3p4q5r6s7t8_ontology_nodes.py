"""Add ontology_nodes and content_ontology tables.

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-03-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "o3p4q5r6s7t8"
down_revision = "n2o3p4q5r6s7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ontology_nodes ────────────────────────────────────────────────────────
    op.create_table(
        "ontology_nodes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "parent_id",
            sa.Integer,
            sa.ForeignKey("ontology_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default="1"),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id"),
            nullable=False,
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
        sa.CheckConstraint("id != parent_id", name="ck_ontology_nodes_no_self_parent"),
    )
    op.create_index(
        "ix_ontology_nodes_parent_id_sort",
        "ontology_nodes",
        ["parent_id", "sort_order"],
    )

    # ── content_ontology ──────────────────────────────────────────────────────
    op.create_table(
        "content_ontology",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ontology_node_id",
            sa.Integer,
            sa.ForeignKey("ontology_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "post_id",
            "ontology_node_id",
            "workspace_id",
            name="uq_content_ontology_post_node_ws",
        ),
    )
    op.create_index(
        "ix_content_ontology_post_ws", "content_ontology", ["post_id", "workspace_id"]
    )
    op.create_index(
        "ix_content_ontology_node_ws",
        "content_ontology",
        ["ontology_node_id", "workspace_id"],
    )


def downgrade() -> None:
    op.drop_table("content_ontology")
    op.drop_table("ontology_nodes")
