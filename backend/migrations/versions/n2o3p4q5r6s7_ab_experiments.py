"""Add ab_experiments and ab_experiment_runs tables.

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-03-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "n2o3p4q5r6s7"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ab_experiments ───────────────────────────────────────────────────────
    op.create_table(
        "ab_experiments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "suite_id",
            sa.Integer,
            sa.ForeignKey("benchmark_suites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Variant A
        sa.Column(
            "variant_a_prompt_post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("variant_a_version", sa.Integer, nullable=False),
        # Variant B
        sa.Column(
            "variant_b_prompt_post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("variant_b_version", sa.Integer, nullable=False),
        sa.Column(
            "status",
            sa.Text,
            sa.CheckConstraint(
                "status IN ('draft','running','completed','canceled')",
                name="ck_ab_experiments_status",
            ),
            nullable=False,
            server_default="draft",
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ab_experiments_workspace_id", "ab_experiments", ["workspace_id"])
    op.create_index("ix_ab_experiments_suite_id", "ab_experiments", ["suite_id"])
    op.create_index("ix_ab_experiments_status", "ab_experiments", ["status"])

    # ── ab_experiment_runs ───────────────────────────────────────────────────
    op.create_table(
        "ab_experiment_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "experiment_id",
            sa.Integer,
            sa.ForeignKey("ab_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_a_id",
            sa.Integer,
            sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_b_id",
            sa.Integer,
            sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("experiment_id", name="uq_ab_experiment_runs_experiment"),
        sa.CheckConstraint("run_a_id <> run_b_id", name="ck_ab_experiment_runs_distinct"),
    )
    op.create_index("ix_ab_experiment_runs_experiment_id", "ab_experiment_runs", ["experiment_id"])


def downgrade() -> None:
    op.drop_table("ab_experiment_runs")
    op.drop_table("ab_experiments")
