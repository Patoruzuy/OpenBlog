"""Fix posts.is_featured column type: Integer → Boolean.

Revision ID: c3f5d2b8e017
Revises: b2e4f1a9c305
Create Date: 2026-02-25 00:01:00.000000

The initial migration created ``posts.is_featured`` as ``INTEGER``.
PostgreSQL treats INTEGER and BOOLEAN as incompatible types — any attempt
to INSERT a Python ``bool`` via SQLAlchemy fails with DatatypeMismatch.
This migration casts the column to BOOLEAN so the ORM works correctly.

On SQLite the column is already stored as 0/1 integer which satisfies
the mapped_column(Boolean) declaration — no-op via batch_alter_table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f5d2b8e017"
down_revision: str | Sequence[str] | None = "b2e4f1a9c305"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Drop the integer default first, cast the column, then restore boolean default.
        op.execute("ALTER TABLE posts ALTER COLUMN is_featured DROP DEFAULT")
        op.execute(
            "ALTER TABLE posts "
            "ALTER COLUMN is_featured TYPE BOOLEAN "
            "USING is_featured::boolean"
        )
        op.execute(
            "ALTER TABLE posts "
            "ALTER COLUMN is_featured SET DEFAULT FALSE"
        )
    else:
        # SQLite: recreate table via batch — column stays as INTEGER
        # but SQLAlchemy's Boolean type maps to 0/1 transparently.
        with op.batch_alter_table("posts") as batch_op:
            batch_op.alter_column(
                "is_featured",
                existing_type=sa.Integer(),
                type_=sa.Boolean(),
                existing_nullable=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE posts "
            "ALTER COLUMN is_featured TYPE INTEGER "
            "USING is_featured::integer"
        )
        op.execute(
            "ALTER TABLE posts "
            "ALTER COLUMN is_featured SET DEFAULT 0"
        )
    else:
        with op.batch_alter_table("posts") as batch_op:
            batch_op.alter_column(
                "is_featured",
                existing_type=sa.Boolean(),
                type_=sa.Integer(),
                existing_nullable=False,
            )
