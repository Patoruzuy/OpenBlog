"""Analytics Explanations: analytics_explanations table.

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-03-03 00:00:00.000000

Schema changes
--------------
1.  Create table ``analytics_explanations``
2.  Unique constraint on
    (scope_type, workspace_id, prompt_post_id, prompt_version, kind, input_fingerprint)
3.  Compound index on (prompt_post_id, workspace_id, created_at)
4.  CHECK constraints on scope_type, kind, status

Reversible: down() drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p4q5r6s7t8u9"
down_revision = "o3p4q5r6s7t8"
branch_labels = None
depends_on = None


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    op.create_table(
        "analytics_explanations",
        sa.Column("id", sa.Integer, primary_key=True),
        # ── Scope ──────────────────────────────────────────────────────────
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # ── Subject ────────────────────────────────────────────────────────
        sa.Column(
            "prompt_post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.Integer, nullable=True),
        # ── Classification ─────────────────────────────────────────────────
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("input_fingerprint", sa.Text, nullable=False),
        # ── Result ─────────────────────────────────────────────────────────
        sa.Column("explanation_md", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        # ── Requester ──────────────────────────────────────────────────────
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # ── Timestamps ─────────────────────────────────────────────────────
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # ── CHECK constraints ───────────────────────────────────────────────
        sa.CheckConstraint(
            "scope_type IN ('public', 'workspace')",
            name="ck_analytics_explanation_scope_type",
        ),
        sa.CheckConstraint(
            "kind IN ('trend', 'fork_rationale', 'version_diff')",
            name="ck_analytics_explanation_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_analytics_explanation_status",
        ),
        # ── Unique constraint ───────────────────────────────────────────────
        sa.UniqueConstraint(
            "scope_type",
            "workspace_id",
            "prompt_post_id",
            "prompt_version",
            "kind",
            "input_fingerprint",
            name="uq_analytics_explanation_fingerprint",
        ),
    )

    # Compound index: fast lookup by post + workspace ordered by recency.
    op.create_index(
        "ix_analytics_explanation_post_ws_time",
        "analytics_explanations",
        ["prompt_post_id", "workspace_id", "created_at"],
    )


# ── downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.drop_index(
        "ix_analytics_explanation_post_ws_time",
        table_name="analytics_explanations",
    )
    op.drop_table("analytics_explanations")
