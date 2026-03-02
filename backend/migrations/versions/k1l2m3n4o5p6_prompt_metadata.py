"""Prompt metadata table.

Revision ID: k1l2m3n4o5p6
Revises: j3k4l5m6n7o8
Create Date: 2026-03-02 00:00:00.000000

Schema changes
--------------
1.  Create table ``prompt_metadata``
    - post_id  INTEGER  PK  FK posts.id ON DELETE CASCADE
    - category  VARCHAR(120)  NOT NULL
    - intended_model  VARCHAR(120)  NULL
    - complexity_level  VARCHAR(20)  NOT NULL  DEFAULT 'intermediate'
    - variables_json  TEXT  NOT NULL  DEFAULT '{}'
    - usage_notes  TEXT  NULL
    - example_input  TEXT  NULL
    - example_output  TEXT  NULL
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "k1l2m3n4o5p6"
down_revision = "j3k4l5m6n7o8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_metadata",
        sa.Column(
            "post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("category", sa.String(120), nullable=False),
        sa.Column("intended_model", sa.String(120), nullable=True),
        sa.Column(
            "complexity_level",
            sa.String(20),
            nullable=False,
            server_default="intermediate",
        ),
        sa.Column(
            "variables_json",
            sa.Text,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("usage_notes", sa.Text, nullable=True),
        sa.Column("example_input", sa.Text, nullable=True),
        sa.Column("example_output", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("prompt_metadata")
