"""AI Suggestion-to-Revision: add suggested_edits_json + revision source metadata.

Revision ID: h1i2j3k4l5m6
Revises: g0h1i2j3k4l5
Create Date: 2026-03-02 00:00:00.000000

Schema changes
--------------
1.  Add column ``suggested_edits_json`` (JSON, NOT NULL, default '{}') to
    ``ai_review_results``.  Stores structured AI-suggested edit operations
    that members can promote to pending Revision proposals.

2.  Add column ``source_metadata_json`` (JSON, nullable) to ``revisions``.
    Populated when a revision is created from an AI suggestion; stores:
      {"source": "ai_suggestion", "ai_review_request_id": <int>, "suggestion_id": "<str>"}

Reversible: downgrade() drops both columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h1i2j3k4l5m6"
down_revision = "g0h1i2j3k4l5"
branch_labels = None
depends_on = None


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # 1. Structured suggested-edit output on completed AI reviews.
    op.add_column(
        "ai_review_results",
        sa.Column(
            "suggested_edits_json",
            sa.JSON,
            nullable=False,
            server_default="{}",
            comment=(
                'Structured AI-suggested edits: '
                '{"edits": [{id, title, kind, target_hint, proposed_markdown, rationale}]}'
            ),
        ),
    )

    # 2. Source attribution on AI-generated revision proposals.
    op.add_column(
        "revisions",
        sa.Column(
            "source_metadata_json",
            sa.JSON,
            nullable=True,
            comment=(
                'AI source attribution: '
                '{"source": "ai_suggestion", "ai_review_request_id": int, "suggestion_id": str}'
            ),
        ),
    )


# ── downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.drop_column("revisions", "source_metadata_json")
    op.drop_column("ai_review_results", "suggested_edits_json")
