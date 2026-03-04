"""Post release notes: changelog entries for accepted revisions.

Adds the ``post_release_notes`` table that records one human-readable
changelog entry every time a revision is accepted and a new PostVersion
snapshot is written.

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-03-01 00:00:00.000000

Schema changes
--------------
1. Create table ``post_release_notes``
2. Create index ``ix_post_release_notes_post_id``
3. Create index ``ix_post_release_notes_accepted_revision_id``
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_release_notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "post_id",
            sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column(
            "accepted_revision_id",
            sa.Integer,
            sa.ForeignKey("revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "auto_generated",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_post_release_notes_post_id", "post_release_notes", ["post_id"])
    op.create_index(
        "ix_post_release_notes_accepted_revision_id",
        "post_release_notes",
        ["accepted_revision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_post_release_notes_accepted_revision_id", table_name="post_release_notes"
    )
    op.drop_index("ix_post_release_notes_post_id", table_name="post_release_notes")
    op.drop_table("post_release_notes")
