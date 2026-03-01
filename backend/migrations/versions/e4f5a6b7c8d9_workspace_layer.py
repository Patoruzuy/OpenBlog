"""Workspace layer: private document containers.

Adds the ``workspaces`` and ``workspace_members`` tables, scopes post slugs
to a (workspace_id, slug) pair via partial unique indexes, and adds the
``workspace_id`` FK column to ``posts``.

Revision ID: e4f5a6b7c8d9
Revises: d3e6f0b2c5a8
Create Date: 2026-06-01 00:00:00.000000

Schema changes
--------------
1.  Create enum  ``workspace_visibility``  (PostgreSQL only)
2.  Create enum  ``workspace_member_role`` (PostgreSQL only)
3.  Create table ``workspaces``
4.  Create table ``workspace_members``
5.  ``posts``: add nullable ``workspace_id`` FK → ``workspaces.id`` SET NULL
6.  ``posts``: drop global unique index on ``slug``
7.  ``posts``: create partial unique index ``uq_posts_public_slug``
             (slug WHERE workspace_id IS NULL)
8.  ``posts``: create partial unique index ``uq_posts_workspace_slug``
             (workspace_id, slug WHERE workspace_id IS NOT NULL)
9.  ``posts``: create plain index ``ix_posts_workspace_id``
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "d3e6f0b2c5a8"
branch_labels = None
depends_on = None


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_postgresql() -> bool:
    conn = op.get_bind()
    return conn.dialect.name == "postgresql"


def _index_exists(index_name: str, table_name: str = "posts") -> bool:
    """Return True when the named index exists on *table_name* (introspection)."""
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return any(
        idx["name"] == index_name for idx in insp.get_indexes(table_name)
    )


# ── upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    # 1-2. Enums (PostgreSQL only; SQLite uses VARCHAR)
    if _is_postgresql():
        op.execute(
            "CREATE TYPE workspace_visibility AS ENUM ('private')"
        )
        op.execute(
            "CREATE TYPE workspace_member_role "
            "AS ENUM ('owner', 'editor', 'contributor', 'viewer')"
        )

    # 3. workspaces table
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "visibility",
            sa.String(20) if not _is_postgresql() else sa.Enum(
                "private", name="workspace_visibility", create_type=False
            ),
            nullable=False,
            server_default="private",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # 4. workspace_members table
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(20) if not _is_postgresql() else sa.Enum(
                "owner", "editor", "contributor", "viewer",
                name="workspace_member_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id",
            name="uq_workspace_members_workspace_user",
        ),
    )
    op.create_index(
        "ix_workspace_members_workspace_user",
        "workspace_members",
        ["workspace_id", "user_id"],
    )

    # 5. posts.workspace_id column
    op.add_column(
        "posts",
        sa.Column(
            "workspace_id",
            sa.Integer,
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
            default=None,
        ),
    )

    # 6. Drop the old global unique index on posts.slug.
    #    The constraint may be named "uq_posts_slug" or manifest as the
    #    column-level unique index "ix_posts_slug" depending on the version
    #    of SQLAlchemy / alembic used to create the schema.
    for candidate in ("uq_posts_slug", "ix_posts_slug"):
        if _index_exists(candidate):
            op.drop_index(candidate, table_name="posts")
            break

    # 7. Partial unique index: public-layer slugs (workspace_id IS NULL)
    op.create_index(
        "uq_posts_public_slug",
        "posts",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
        sqlite_where=sa.text("workspace_id IS NULL"),
    )

    # 8. Partial unique index: workspace-layer slugs (workspace_id IS NOT NULL)
    op.create_index(
        "uq_posts_workspace_slug",
        "posts",
        ["workspace_id", "slug"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
        sqlite_where=sa.text("workspace_id IS NOT NULL"),
    )

    # 9. Plain index on workspace_id for FK lookups.
    op.create_index("ix_posts_workspace_id", "posts", ["workspace_id"])


# ── downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    # Reverse posts changes first.
    op.drop_index("ix_posts_workspace_id", table_name="posts")
    if _index_exists("uq_posts_workspace_slug"):
        op.drop_index("uq_posts_workspace_slug", table_name="posts")
    if _index_exists("uq_posts_public_slug"):
        op.drop_index("uq_posts_public_slug", table_name="posts")

    # Restore the original global unique constraint on slug.
    op.create_index("uq_posts_slug", "posts", ["slug"], unique=True)

    op.drop_column("posts", "workspace_id")

    # Drop workspace tables.
    op.drop_index("ix_workspace_members_workspace_user", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")

    # Drop enums on PostgreSQL.
    if _is_postgresql():
        op.execute("DROP TYPE IF EXISTS workspace_member_role")
        op.execute("DROP TYPE IF EXISTS workspace_visibility")
