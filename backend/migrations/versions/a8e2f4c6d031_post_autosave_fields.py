"""Add autosave fields to posts table.

Adds ``last_autosaved_at`` (nullable datetime) and ``autosave_revision``
(integer, NOT NULL, default 0) to support the background autosave feature.

Revision ID: a8e2f4c6d031
Revises: f5a8d2c6e047
Create Date: 2026-02-28 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a8e2f4c6d031"
down_revision = "f5a8d2c6e047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_autosaved_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Set by the autosave endpoint; NULL until first autosave.",
            )
        )
        batch_op.add_column(
            sa.Column(
                "autosave_revision",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="Optimistic concurrency token; incremented on each autosave write.",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_column("autosave_revision")
        batch_op.drop_column("last_autosaved_at")
