"""AI Review Engine: ai_review_requests and ai_review_results tables.

Revision ID: g0h1i2j3k4l5
Revises: c0d1e2f3a4b5
Create Date: 2026-03-01 00:00:00.000000

Schema changes
--------------
1.  Create table ``ai_review_requests``
2.  Create table ``ai_review_results``
3.  Indexes on ``ai_review_requests`` (workspace_id/created_at, post_id/created_at,
    status/created_at, requested_by_user_id/created_at, input_fingerprint)
4.  Unique index on ``ai_review_results.request_id``

Reversible: down() drops both tables in dependent order.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g0h1i2j3k4l5"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # 1. ai_review_requests
    op.create_table(
        "ai_review_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        # Workspace scope (nullable at DB level; service enforces NOT NULL for v1)
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # Subject document
        sa.Column(
            "post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Optional revision to diff-review
        sa.Column(
            "revision_id",
            sa.Integer,
            sa.ForeignKey("revisions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # Reserved for future compare-two-versions mode
        sa.Column("post_version_from", sa.Integer, nullable=True),
        sa.Column("post_version_to", sa.Integer, nullable=True),
        # Requester
        sa.Column(
            "requested_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Classification
        sa.Column("review_type", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "priority",
            sa.SmallInteger,
            nullable=False,
            server_default="0",
        ),
        # Dedup fingerprint (SHA-256 hex; no DB UNIQUE constraint)
        sa.Column("input_fingerprint", sa.Text, nullable=False),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Error log
        sa.Column("error_message", sa.Text, nullable=True),
    )

    # Indexes on ai_review_requests
    op.create_index(
        "ix_ai_review_req_workspace_time",
        "ai_review_requests",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_ai_review_req_post_time",
        "ai_review_requests",
        ["post_id", "created_at"],
    )
    op.create_index(
        "ix_ai_review_req_status_time",
        "ai_review_requests",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_ai_review_req_user_time",
        "ai_review_requests",
        ["requested_by_user_id", "created_at"],
    )
    op.create_index(
        "ix_ai_review_req_fingerprint",
        "ai_review_requests",
        ["input_fingerprint"],
    )

    # 2. ai_review_results
    #
    # findings_json and metrics_json use sa.JSON which maps to JSON on
    # PostgreSQL (JSONB can be added as a follow-up migration if GIN
    # indexing is needed at scale) and TEXT on SQLite (for unit tests).
    op.create_table(
        "ai_review_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer,
            sa.ForeignKey("ai_review_requests.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # Provider metadata
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column(
            "prompt_version",
            sa.String(64),
            nullable=False,
            server_default="ai-review-v1",
        ),
        # Output
        sa.Column("summary_md", sa.Text, nullable=False, server_default=""),
        sa.Column("findings_json", sa.JSON, nullable=False),
        sa.Column("metrics_json", sa.JSON, nullable=False),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


# ── downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    # Drop in FK-dependency order: results → requests
    op.drop_table("ai_review_results")

    op.drop_index("ix_ai_review_req_fingerprint", table_name="ai_review_requests")
    op.drop_index("ix_ai_review_req_user_time", table_name="ai_review_requests")
    op.drop_index("ix_ai_review_req_status_time", table_name="ai_review_requests")
    op.drop_index("ix_ai_review_req_post_time", table_name="ai_review_requests")
    op.drop_index("ix_ai_review_req_workspace_time", table_name="ai_review_requests")

    op.drop_table("ai_review_requests")
