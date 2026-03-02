"""Add benchmark_suites, benchmark_cases, benchmark_runs, benchmark_run_results.

Revision ID: m1n2o3p4q5r6
Revises: l2m3n4o5p6q7
Create Date: 2026-03-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m1n2o3p4q5r6"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── benchmark_suites ─────────────────────────────────────────────────────
    op.create_table(
        "benchmark_suites",
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
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_benchmark_suites_workspace_id", "benchmark_suites", ["workspace_id"])
    op.create_index("ix_benchmark_suites_slug", "benchmark_suites", ["slug"])

    # ── benchmark_cases ──────────────────────────────────────────────────────
    op.create_table(
        "benchmark_cases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "suite_id",
            sa.Integer,
            sa.ForeignKey("benchmark_suites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("input_json", sa.JSON, nullable=False),
        sa.Column("expected_output", sa.Text, nullable=True),
        sa.Column("expected_assertions_json", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_benchmark_cases_suite_id", "benchmark_cases", ["suite_id"])

    # ── benchmark_runs ───────────────────────────────────────────────────────
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "suite_id",
            sa.Integer,
            sa.ForeignKey("benchmark_suites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prompt_post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.Integer, nullable=False),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("model_name", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.Text,
            sa.CheckConstraint(
                "status IN ('queued','running','completed','failed','canceled')",
                name="ck_benchmark_runs_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
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
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_benchmark_runs_suite_id", "benchmark_runs", ["suite_id"])
    op.create_index("ix_benchmark_runs_prompt_post_id", "benchmark_runs", ["prompt_post_id"])
    op.create_index("ix_benchmark_runs_workspace_id", "benchmark_runs", ["workspace_id"])
    op.create_index("ix_benchmark_runs_status", "benchmark_runs", ["status"])

    # ── benchmark_run_results ────────────────────────────────────────────────
    op.create_table(
        "benchmark_run_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer,
            sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            sa.Integer,
            sa.ForeignKey("benchmark_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("output_text", sa.Text, nullable=False),
        sa.Column("score_numeric", sa.Numeric, nullable=True),
        sa.Column("score_details_json", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("run_id", "case_id", name="uq_benchmark_run_results_run_case"),
    )
    op.create_index("ix_benchmark_run_results_run_id", "benchmark_run_results", ["run_id"])
    op.create_index("ix_benchmark_run_results_case_id", "benchmark_run_results", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_run_results_case_id", table_name="benchmark_run_results")
    op.drop_index("ix_benchmark_run_results_run_id", table_name="benchmark_run_results")
    op.drop_table("benchmark_run_results")

    op.drop_index("ix_benchmark_runs_status", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_workspace_id", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_prompt_post_id", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_suite_id", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")

    op.drop_index("ix_benchmark_cases_suite_id", table_name="benchmark_cases")
    op.drop_table("benchmark_cases")

    op.drop_index("ix_benchmark_suites_slug", table_name="benchmark_suites")
    op.drop_index("ix_benchmark_suites_workspace_id", table_name="benchmark_suites")
    op.drop_table("benchmark_suites")
