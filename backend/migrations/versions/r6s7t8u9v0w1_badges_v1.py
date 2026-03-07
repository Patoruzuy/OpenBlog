"""Badges v1 — category, threshold, workspace scope.

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-03-06 00:00:00.000000

Schema changes
--------------
1. badges
   - Add column  category   TEXT NOT NULL DEFAULT 'contribution'
   - Add column  threshold  INTEGER NULL

2. user_badges
   - Add column  workspace_id  BIGINT NULL FK→workspaces ON DELETE CASCADE
   - Drop old unique constraint ``uq_user_badges_pair``  (user_id, badge_id)
   - Create partial unique index ``uq_user_badges_public``
       ON user_badges(user_id, badge_id) WHERE workspace_id IS NULL
   - Create partial unique index ``uq_user_badges_ws``
       ON user_badges(user_id, badge_id, workspace_id) WHERE workspace_id IS NOT NULL
   - Create index ``ix_user_badges_user_awarded``
       ON user_badges(user_id, awarded_at DESC)

Reversible: down() reverts all changes in dependency order.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r6s7t8u9v0w1"
down_revision = "q5r6s7t8u9v0"
branch_labels = None
depends_on = None


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── badges: add category + threshold ─────────────────────────────────
    op.add_column(
        "badges",
        sa.Column(
            "category",
            sa.Text,
            nullable=False,
            server_default="contribution",
        ),
    )
    op.add_column(
        "badges",
        sa.Column("threshold", sa.Integer, nullable=True),
    )

    # ── user_badges: add workspace_id column ──────────────────────────────
    op.add_column(
        "user_badges",
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    # ── user_badges: swap uniqueness from simple → partial ────────────────
    # Drop old simple unique constraint (user_id, badge_id).
    op.drop_constraint("uq_user_badges_pair", "user_badges", type_="unique")

    # Public user-badge: unique per (user_id, badge_id) where workspace IS NULL.
    op.create_index(
        "uq_user_badges_public",
        "user_badges",
        ["user_id", "badge_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    # Workspace user-badge: unique per (user_id, badge_id, workspace_id) where
    # workspace IS NOT NULL (a user may hold the same badge in different workspaces
    # if the badge is scoped, but not twice in the same workspace).
    op.create_index(
        "uq_user_badges_ws",
        "user_badges",
        ["user_id", "badge_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )

    # Performance index: list badges by user, newest first.
    op.create_index(
        "ix_user_badges_user_awarded",
        "user_badges",
        ["user_id", sa.text("awarded_at DESC")],
    )


# ── downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.drop_index("ix_user_badges_user_awarded", table_name="user_badges")
    op.drop_index("uq_user_badges_ws", table_name="user_badges")
    op.drop_index("uq_user_badges_public", table_name="user_badges")

    # Restore original simple unique constraint.
    op.create_unique_constraint(
        "uq_user_badges_pair", "user_badges", ["user_id", "badge_id"]
    )

    op.drop_column("user_badges", "workspace_id")
    op.drop_column("badges", "threshold")
    op.drop_column("badges", "category")
