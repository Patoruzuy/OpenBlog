"""Add functional indexes on users.username and users.display_name for search.

Revision ID: f5a8d2c6e047
Revises: c1d3e5f7a9b2
Create Date: 2026-02-27 00:00:00.000000

Notes
-----
For SQLite (dev/test) these are regular functional B-tree indexes.
For PostgreSQL (production) consider adding a pg_trgm GIN index on top of
these for optimal ``LIKE '%q%'`` performance:

    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX CONCURRENTLY ix_users_username_trgm
        ON users USING GIN (lower(username) gin_trgm_ops);
    CREATE INDEX CONCURRENTLY ix_users_displayname_trgm
        ON users USING GIN (lower(display_name) gin_trgm_ops);
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "f5a8d2c6e047"
down_revision = "c1d3e5f7a9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Functional index on lower(username) — helps prefix / ILIKE queries.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_username_lower ON users (lower(username))"
    )
    # Functional index on lower(display_name) — nullable column.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_displayname_lower "
        "ON users (lower(display_name))"
    )
    # Plain index on headline (searchable text column).
    with op.batch_alter_table("users") as batch_op:
        batch_op.create_index(
            "ix_users_headline",
            ["headline"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_headline")
    op.execute("DROP INDEX IF EXISTS ix_users_displayname_lower")
    op.execute("DROP INDEX IF EXISTS ix_users_username_lower")
