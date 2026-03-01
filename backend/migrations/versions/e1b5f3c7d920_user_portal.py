"""add user portal tables and columns

Revision ID: e1b5f3c7d920
Revises: d4a7e3c9f128
Create Date: 2025-01-01 00:00:00.000000

Adds all database artifacts required by the User Portal feature:

New tables
----------
user_privacy_settings   — per-user visibility + identity mode config
user_social_links       — ordered list of curated social/external links
user_connected_accounts — OAuth provider linked accounts (GitHub, etc.)
user_repositories       — manually added or API-synced code repos

New columns on ``users``
------------------------
headline VARCHAR(200)  — short tagline shown under the display name

New columns on ``revisions``
----------------------------
public_identity_mode          VARCHAR(20)  — identity mode snapshot
public_display_name_snapshot  VARCHAR(200) — display name / alias snapshot
public_avatar_snapshot        VARCHAR(512) — avatar URL snapshot

New columns on ``comments``
---------------------------
public_identity_mode          VARCHAR(20)  — identity mode snapshot
public_display_name_snapshot  VARCHAR(200) — display name / alias snapshot
public_avatar_snapshot        VARCHAR(512) — avatar URL snapshot
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e1b5f3c7d920"
down_revision = "d4a7e3c9f128"
branch_labels = None
depends_on = None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _is_pg() -> bool:
    return op.get_context().dialect.name == "postgresql"


# ──────────────────────────────────────────────────────────────────────────────
# upgrade
# ──────────────────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. user_privacy_settings ─────────────────────────────────────────────
    op.create_table(
        "user_privacy_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "profile_visibility",
            sa.Enum("public", "members", "private", name="profile_visibility_enum"),
            nullable=False,
            server_default="public",
        ),
        sa.Column(
            "default_identity_mode",
            sa.Enum("public", "pseudonymous", "anonymous", name="identity_mode_enum"),
            nullable=False,
            server_default="public",
        ),
        sa.Column("pseudonymous_alias", sa.String(length=80), nullable=True),
        sa.Column(
            "show_avatar", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("show_bio", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "show_location", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "show_social_links", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "show_repositories", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "show_contributions", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "searchable_profile", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_user_privacy_settings_user_id",
        "user_privacy_settings",
        ["user_id"],
    )

    # ── 3. user_social_links ──────────────────────────────────────────────────
    op.create_table(
        "user_social_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("icon_slug", sa.String(length=40), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_social_links_user_id", "user_social_links", ["user_id"])

    # ── 4. user_connected_accounts ────────────────────────────────────────────
    op.create_table(
        "user_connected_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_user_id", sa.String(length=200), nullable=True),
        sa.Column("provider_username", sa.String(length=200), nullable=True),
        sa.Column("provider_profile_url", sa.String(length=500), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_connected_user_provider"),
    )
    op.create_index(
        "ix_user_connected_accounts_user_id",
        "user_connected_accounts",
        ["user_id"],
    )

    # ── 5. user_repositories ─────────────────────────────────────────────────
    op.create_table(
        "user_repositories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "manual", "github", "gitlab", "other", name="repository_source_enum"
            ),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("repo_name", sa.String(length=200), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=60), nullable=True),
        sa.Column("stars_cached", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("forks_cached", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_featured", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_repo_id", sa.String(length=100), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_repositories_user_id", "user_repositories", ["user_id"])

    # ── 6. New column on users ────────────────────────────────────────────────
    op.add_column("users", sa.Column("headline", sa.String(length=200), nullable=True))

    # ── 7. New columns on revisions ───────────────────────────────────────────
    op.add_column(
        "revisions",
        sa.Column("public_identity_mode", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "revisions",
        sa.Column("public_display_name_snapshot", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "revisions",
        sa.Column("public_avatar_snapshot", sa.String(length=512), nullable=True),
    )

    # ── 8. New columns on comments ────────────────────────────────────────────
    op.add_column(
        "comments",
        sa.Column("public_identity_mode", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "comments",
        sa.Column("public_display_name_snapshot", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "comments",
        sa.Column("public_avatar_snapshot", sa.String(length=512), nullable=True),
    )


# ──────────────────────────────────────────────────────────────────────────────
# downgrade
# ──────────────────────────────────────────────────────────────────────────────


def downgrade() -> None:
    # ── Snapshot columns ──────────────────────────────────────────────────────
    op.drop_column("comments", "public_avatar_snapshot")
    op.drop_column("comments", "public_display_name_snapshot")
    op.drop_column("comments", "public_identity_mode")
    op.drop_column("revisions", "public_avatar_snapshot")
    op.drop_column("revisions", "public_display_name_snapshot")
    op.drop_column("revisions", "public_identity_mode")
    op.drop_column("users", "headline")

    # ── Portal tables ─────────────────────────────────────────────────────────
    op.drop_index("ix_user_repositories_user_id", table_name="user_repositories")
    op.drop_table("user_repositories")

    op.drop_index(
        "ix_user_connected_accounts_user_id", table_name="user_connected_accounts"
    )
    op.drop_table("user_connected_accounts")

    op.drop_index("ix_user_social_links_user_id", table_name="user_social_links")
    op.drop_table("user_social_links")

    op.drop_index(
        "ix_user_privacy_settings_user_id", table_name="user_privacy_settings"
    )
    op.drop_table("user_privacy_settings")

    # ── Enum types (PostgreSQL only) ──────────────────────────────────────────
    if _is_pg():
        op.execute("DROP TYPE IF EXISTS repository_source_enum")
        op.execute("DROP TYPE IF EXISTS identity_mode_enum")
        op.execute("DROP TYPE IF EXISTS profile_visibility_enum")
