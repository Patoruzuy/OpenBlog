"""Playbook templates: global template library + posts.kind discriminator.

Revision ID: c0d1e2f3a4b5
Revises: b3c4d5e6f7a8
Create Date: 2026-06-10 00:00:00.000000

Schema changes
--------------
1.  Create table ``playbook_templates``
2.  Create table ``playbook_template_versions``
3.  ``posts``: add ``kind`` VARCHAR(20) NOT NULL DEFAULT 'article'
4.  ``posts``: add nullable ``template_id`` FK → ``playbook_templates.id``
5.  ``posts``: add nullable ``template_version_id`` FK → ``playbook_template_versions.id``
6.  Create index ``ix_posts_kind``
7.  Create index ``ix_posts_workspace_kind``
8.  Create index ``ix_playbook_templates_public``
9.  Create index ``ix_playbook_template_versions_template``
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c0d1e2f3a4b5"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


# ── helpers ───────────────────────────────────────────────────────────────────


def _is_postgresql() -> bool:
    conn = op.get_bind()
    return conn.dialect.name == "postgresql"


# ── upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # 1. playbook_templates — global template library
    op.create_table(
        "playbook_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # 2. playbook_template_versions — immutable version snapshots
    op.create_table(
        "playbook_template_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "template_id",
            sa.Integer,
            sa.ForeignKey("playbook_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("schema_json", sa.Text, nullable=True),
        sa.Column("skeleton_md", sa.Text, nullable=True),
        sa.Column("change_notes", sa.String(1024), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("template_id", "version", name="uq_ptv_template_version"),
    )

    # 3. posts.kind discriminator column
    op.add_column(
        "posts",
        sa.Column(
            "kind",
            sa.String(20),
            nullable=False,
            server_default="article",
        ),
    )

    # 4. posts.template_id
    op.add_column(
        "posts",
        sa.Column(
            "template_id",
            sa.Integer,
            sa.ForeignKey("playbook_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 5. posts.template_version_id
    op.add_column(
        "posts",
        sa.Column(
            "template_version_id",
            sa.Integer,
            sa.ForeignKey("playbook_template_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 6. Index on posts.kind
    op.create_index("ix_posts_kind", "posts", ["kind"])

    # 7. Composite index for workspace playbook queries
    op.create_index("ix_posts_workspace_kind", "posts", ["workspace_id", "kind"])

    # 8. Index on playbook_templates.is_public
    op.create_index("ix_playbook_templates_public", "playbook_templates", ["is_public"])

    # 9. Index for fast latest-version lookups
    op.create_index(
        "ix_playbook_template_versions_template",
        "playbook_template_versions",
        ["template_id", "version"],
    )


# ── downgrade ──────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.drop_index(
        "ix_playbook_template_versions_template", "playbook_template_versions"
    )
    op.drop_index("ix_playbook_templates_public", "playbook_templates")
    op.drop_index("ix_posts_workspace_kind", "posts")
    op.drop_index("ix_posts_kind", "posts")

    op.drop_column("posts", "template_version_id")
    op.drop_column("posts", "template_id")
    op.drop_column("posts", "kind")

    op.drop_table("playbook_template_versions")
    op.drop_table("playbook_templates")
