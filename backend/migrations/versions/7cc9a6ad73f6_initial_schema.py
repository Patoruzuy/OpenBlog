"""initial schema

Revision ID: 7cc9a6ad73f6
Revises:
Create Date: 2025-01-01 00:00:00.000000

Creates all tables for the OpenBlog platform:
  users, posts, tags, post_tags, post_versions,
  revisions, comments, votes, bookmarks, follows,
  badges, user_badges, notifications, analytics_events

Also installs the tsvector trigger on posts.search_vector.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7cc9a6ad73f6"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all tables and indexes."""

    # ── Enum types (PostgreSQL only) ───────────────────────────────────────
    # sa.Enum creates the TYPE in PG; SQLite ignores it.
    user_role = sa.Enum(
        "admin", "editor", "contributor", "reader",
        name="user_role",
    )
    post_status = sa.Enum(
        "draft", "published", "scheduled", "archived",
        name="post_status",
    )
    revision_status = sa.Enum(
        "pending", "accepted", "rejected",
        name="revision_status",
    )
    vote_target = sa.Enum(
        "post", "comment",
        name="vote_target_type",
    )

    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        # Identity
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("password_hash", sa.String(256), nullable=True),
        # OAuth
        sa.Column("oauth_provider", sa.String(32), nullable=True),
        sa.Column("oauth_id", sa.String(128), nullable=True),
        # Role & status
        sa.Column(
            "role", user_role, nullable=False,
            server_default="reader",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_email_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_shadow_banned", sa.Boolean, nullable=False, server_default=sa.false()),
        # Reputation
        sa.Column("reputation_score", sa.Integer, nullable=False, server_default="0"),
        # Profile
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("website_url", sa.String(512), nullable=True),
        sa.Column("github_url", sa.String(512), nullable=True),
        sa.Column("tech_stack", sa.Text, nullable=True),
        sa.Column("location", sa.String(128), nullable=True),
        # Timestamps
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
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index(
        "ix_users_oauth", "users", ["oauth_provider", "oauth_id"], unique=True
    )

    # ── posts ──────────────────────────────────────────────────────────────
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer, primary_key=True),
        # Content
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("markdown_body", sa.Text, nullable=False, server_default=""),
        # Status & versioning
        sa.Column(
            "status", post_status, nullable=False, server_default="draft"
        ),
        sa.Column(
            "version", sa.Integer, nullable=False, server_default="1",
            comment="Monotonically increasing; bumped on every accepted revision."
        ),
        sa.Column("is_featured", sa.Boolean, nullable=False, server_default=sa.false()),
        # Scheduling
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        # Metrics
        sa.Column("reading_time_minutes", sa.Integer, nullable=False, server_default="1"),
        sa.Column("view_count", sa.Integer, nullable=False, server_default="0"),
        # SEO
        sa.Column("seo_title", sa.String(512), nullable=True),
        sa.Column("seo_description", sa.String(1024), nullable=True),
        sa.Column("canonical_url", sa.String(512), nullable=True),
        sa.Column("og_image_url", sa.String(512), nullable=True),
        # Authorship
        sa.Column(
            "author_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Timestamps
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
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_posts_slug", "posts", ["slug"], unique=True)
    op.create_index("ix_posts_author_id", "posts", ["author_id"])
    op.create_index("ix_posts_status", "posts", ["status"])
    op.create_index("ix_posts_published_at", "posts", ["published_at"])

    # tsvector column + GIN index (PostgreSQL only; silently skipped on SQLite)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS "
            "search_vector tsvector GENERATED ALWAYS AS ("
            "  to_tsvector('english', coalesce(title, '') || ' ' || coalesce(markdown_body, ''))"
            ") STORED"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_posts_search_vector "
            "ON posts USING gin(search_vector)"
        )

    # ── tags ───────────────────────────────────────────────────────────────
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("color", sa.String(7), nullable=True, comment="Hex colour, e.g. #3776ab"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_tags_name", "tags", ["name"], unique=True)
    op.create_index("ix_tags_slug", "tags", ["slug"], unique=True)

    # ── post_tags (M2M association) ────────────────────────────────────────
    op.create_table(
        "post_tags",
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id", sa.Integer,
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ── post_versions ──────────────────────────────────────────────────────
    op.create_table(
        "post_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "accepted_by_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("markdown_body", sa.Text, nullable=False),
        sa.Column("change_summary", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("post_id", "version_number", name="uq_post_versions_post_version"),
    )
    op.create_index("ix_post_versions_post_id", "post_versions", ["post_id"])
    op.create_index("ix_post_versions_author_id", "post_versions", ["author_id"])

    # ── revisions ──────────────────────────────────────────────────────────
    op.create_table(
        "revisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "base_version_id", sa.Integer,
            sa.ForeignKey("post_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposed_markdown", sa.Text, nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column(
            "status", revision_status, nullable=False, server_default="pending"
        ),
        sa.Column("diff", sa.JSON, nullable=True, comment="Derived unified diff (cached)"),
        sa.Column(
            "reviewed_by_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_note", sa.Text, nullable=True),
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
    )
    op.create_index("ix_revisions_post_id", "revisions", ["post_id"])
    op.create_index("ix_revisions_author_id", "revisions", ["author_id"])
    op.create_index("ix_revisions_status", "revisions", ["status"])

    # ── comments ───────────────────────────────────────────────────────────
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id", sa.Integer,
            sa.ForeignKey("comments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_flagged", sa.Boolean, nullable=False, server_default=sa.false()),
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
    )
    op.create_index("ix_comments_post_id", "comments", ["post_id"])
    op.create_index("ix_comments_author_id", "comments", ["author_id"])
    op.create_index("ix_comments_parent_id", "comments", ["parent_id"])

    # ── votes ──────────────────────────────────────────────────────────────
    op.create_table(
        "votes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", vote_target, nullable=False),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "target_type", "target_id",
            name="uq_votes_user_target",
        ),
    )
    op.create_index("ix_votes_user_id", "votes", ["user_id"])
    op.create_index("ix_votes_target", "votes", ["target_type", "target_id"])

    # ── bookmarks ──────────────────────────────────────────────────────────
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "post_id", name="uq_bookmarks_user_post"),
    )
    op.create_index("ix_bookmarks_user_id", "bookmarks", ["user_id"])

    # ── follows ────────────────────────────────────────────────────────────
    op.create_table(
        "follows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "follower_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "following_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("follower_id", "following_id", name="uq_follows_pair"),
    )
    op.create_index("ix_follows_follower_id", "follows", ["follower_id"])
    op.create_index("ix_follows_following_id", "follows", ["following_id"])

    # ── badges ─────────────────────────────────────────────────────────────
    op.create_table(
        "badges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon_url", sa.String(512), nullable=True),
        sa.Column("points_value", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_badges_key", "badges", ["key"], unique=True)

    # ── user_badges ────────────────────────────────────────────────────────
    op.create_table(
        "user_badges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "badge_id", sa.Integer,
            sa.ForeignKey("badges.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "awarded_by_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "revision_id", sa.Integer,
            sa.ForeignKey("revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "awarded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "badge_id", name="uq_user_badges_pair"),
    )
    op.create_index("ix_user_badges_user_id", "user_badges", ["user_id"])

    # ── notifications ──────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Not a DB Enum — new types can be added without a migration.
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default=sa.false()),
        # Optional context links
        sa.Column(
            "actor_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "revision_id", sa.Integer,
            sa.ForeignKey("revisions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_is_read", "notifications", ["user_id", "is_read"])

    # ── analytics_events ───────────────────────────────────────────────────
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "post_id", sa.Integer,
            sa.ForeignKey("posts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id", sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("referrer", sa.String(1024), nullable=True),
        sa.Column("path", sa.String(512), nullable=True),
        sa.Column("extra", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_analytics_events_event_type", "analytics_events", ["event_type"])
    op.create_index("ix_analytics_events_post_id", "analytics_events", ["post_id"])
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"])


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_posts_search_vector")

    op.drop_table("analytics_events")
    op.drop_table("notifications")
    op.drop_table("user_badges")
    op.drop_table("badges")
    op.drop_table("follows")
    op.drop_table("bookmarks")
    op.drop_table("votes")
    op.drop_table("comments")
    op.drop_table("revisions")
    op.drop_table("post_versions")
    op.drop_table("post_tags")
    op.drop_table("tags")
    op.drop_table("posts")
    op.drop_table("users")

    # Drop enum types (PostgreSQL only)
    sa.Enum(name="vote_target_type").drop(bind, checkfirst=True)
    sa.Enum(name="revision_status").drop(bind, checkfirst=True)
    sa.Enum(name="post_status").drop(bind, checkfirst=True)
    sa.Enum(name="user_role").drop(bind, checkfirst=True)
