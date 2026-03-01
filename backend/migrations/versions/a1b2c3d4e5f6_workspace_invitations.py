"""Workspace invitations: token-gated membership invitations.

Adds the ``workspace_invitations`` table with SHA-256 hashed tokens,
expiry/revocation tracking, and multi-use support.

Revision ID: a1b2c3d4e5f6
Revises: e4f5a6b7c8d9
Create Date: 2026-03-01 00:00:00.000000

Schema changes
--------------
1. Create table ``workspace_invitations``
2. Create index ``idx_workspace_invites_workspace_id``
3. Create index ``idx_workspace_invites_token_hash``
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invited_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # 64-char SHA-256 hex digest — never the raw token.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        # 'editor' | 'contributor' | 'viewer'
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("max_uses", sa.Integer, nullable=False, server_default="1"),
        sa.Column("uses", sa.Integer, nullable=False, server_default="0"),
        # Constraints
        sa.UniqueConstraint("token_hash", name="uq_workspace_invitations_token_hash"),
        sa.CheckConstraint(
            "role IN ('editor', 'contributor', 'viewer')",
            name="ck_workspace_invitations_role",
        ),
        sa.CheckConstraint(
            "max_uses >= 1",
            name="ck_workspace_invitations_max_uses",
        ),
        sa.CheckConstraint(
            "uses >= 0",
            name="ck_workspace_invitations_uses_nonneg",
        ),
    )

    op.create_index(
        "idx_workspace_invites_workspace_id",
        "workspace_invitations",
        ["workspace_id"],
    )
    op.create_index(
        "idx_workspace_invites_token_hash",
        "workspace_invitations",
        ["token_hash"],
    )


def downgrade() -> None:
    op.drop_index("idx_workspace_invites_token_hash", table_name="workspace_invitations")
    op.drop_index("idx_workspace_invites_workspace_id", table_name="workspace_invitations")
    op.drop_table("workspace_invitations")
