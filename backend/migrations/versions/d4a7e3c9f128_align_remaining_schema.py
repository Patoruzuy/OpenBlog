"""align remaining schema

Revision ID: d4a7e3c9f128
Revises: c3f5d2b8e017
Create Date: 2025-01-01 00:00:00.000000

Brings the live PostgreSQL schema in line with the final ORM models for the
six tables that drifted between the initial migration and the final model
definitions:

  follows       – rename following_id → followed_id
  notifications – rename type→notification_type; drop {url,actor_id,post_id,
                  revision_id}; add payload TEXT, read_at TIMESTAMPTZ
  post_versions – drop {author_id, change_summary}; add revision_id INT FK
  revisions     – rename diff→diff_cache, reviewer_note→rejection_note;
                  add base_version_number INT, reviewed_at TIMESTAMPTZ
  badges        – drop {points_value, created_at}
  user_badges   – drop {awarded_by_id, post_id, revision_id}
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4a7e3c9f128"
down_revision = "c3f5d2b8e017"
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
    # ── 1. follows ────────────────────────────────────────────────────────────
    # Rename following_id → followed_id (and the unique constraint that names it)
    if _is_pg():
        op.execute("ALTER TABLE follows RENAME COLUMN following_id TO followed_id")
        # Alembic/PG doesn't automatically rename the index; recreate to be safe.
        op.execute(
            "DO $$ BEGIN "
            "  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_follows_pair') THEN "
            "    ALTER TABLE follows DROP CONSTRAINT uq_follows_pair; "
            "  END IF; "
            "END $$"
        )
        op.create_unique_constraint(
            "uq_follows_pair", "follows", ["follower_id", "followed_id"]
        )
    else:
        with op.batch_alter_table("follows", schema=None) as batch:
            batch.alter_column("following_id", new_column_name="followed_id")

    # ── 2. notifications ──────────────────────────────────────────────────────
    if _is_pg():
        # Drop index on the column we're about to rename (if it exists)
        op.execute("DROP INDEX IF EXISTS ix_notifications_user_read")
        op.execute("ALTER TABLE notifications RENAME COLUMN type TO notification_type")
        # Drop extra columns that the ORM no longer maps
        for col in ("url", "actor_id", "post_id", "revision_id"):
            op.execute(f"ALTER TABLE notifications DROP COLUMN IF EXISTS {col}")
        # Add new ORM columns
        op.add_column("notifications", sa.Column("payload", sa.Text(), nullable=True))
        op.add_column(
            "notifications",
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        )
        # Recreate the composite index the ORM declares
        op.create_index(
            "ix_notifications_user_read",
            "notifications",
            ["user_id", "is_read"],
        )
    else:
        with op.batch_alter_table("notifications", schema=None) as batch:
            batch.alter_column("type", new_column_name="notification_type")
            for col in ("url", "actor_id", "post_id", "revision_id"):
                try:
                    batch.drop_column(col)
                except Exception:
                    pass
            batch.add_column(sa.Column("payload", sa.Text(), nullable=True))
            batch.add_column(
                sa.Column("read_at", sa.DateTime(timezone=True), nullable=True)
            )

    # ── 3. post_versions ──────────────────────────────────────────────────────
    if _is_pg():
        op.execute("ALTER TABLE post_versions DROP COLUMN IF EXISTS author_id")
        op.execute("ALTER TABLE post_versions DROP COLUMN IF EXISTS change_summary")
        op.add_column(
            "post_versions",
            sa.Column(
                "revision_id",
                sa.Integer(),
                sa.ForeignKey("revisions.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    else:
        with op.batch_alter_table("post_versions", schema=None) as batch:
            try:
                batch.drop_column("author_id")
            except Exception:
                pass
            try:
                batch.drop_column("change_summary")
            except Exception:
                pass
            batch.add_column(
                sa.Column(
                    "revision_id",
                    sa.Integer(),
                    sa.ForeignKey("revisions.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )

    # ── 4. revisions ──────────────────────────────────────────────────────────
    if _is_pg():
        op.execute("ALTER TABLE revisions RENAME COLUMN diff TO diff_cache")
        op.execute(
            "ALTER TABLE revisions RENAME COLUMN reviewer_note TO rejection_note"
        )
        op.add_column(
            "revisions",
            sa.Column("base_version_number", sa.Integer(), nullable=True),
        )
        op.add_column(
            "revisions",
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )
    else:
        with op.batch_alter_table("revisions", schema=None) as batch:
            batch.alter_column("diff", new_column_name="diff_cache")
            batch.alter_column("reviewer_note", new_column_name="rejection_note")
            batch.add_column(
                sa.Column("base_version_number", sa.Integer(), nullable=True)
            )
            batch.add_column(
                sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
            )

    # ── 5. badges ─────────────────────────────────────────────────────────────
    if _is_pg():
        op.execute("ALTER TABLE badges DROP COLUMN IF EXISTS points_value")
        op.execute("ALTER TABLE badges DROP COLUMN IF EXISTS created_at")
    else:
        with op.batch_alter_table("badges", schema=None) as batch:
            for col in ("points_value", "created_at"):
                try:
                    batch.drop_column(col)
                except Exception:
                    pass

    # ── 6. user_badges ────────────────────────────────────────────────────────
    if _is_pg():
        for col in ("awarded_by_id", "post_id", "revision_id"):
            op.execute(f"ALTER TABLE user_badges DROP COLUMN IF EXISTS {col}")
    else:
        with op.batch_alter_table("user_badges", schema=None) as batch:
            for col in ("awarded_by_id", "post_id", "revision_id"):
                try:
                    batch.drop_column(col)
                except Exception:
                    pass


# ──────────────────────────────────────────────────────────────────────────────
# downgrade  (best-effort — restores columns but cannot recover dropped data)
# ──────────────────────────────────────────────────────────────────────────────


def downgrade() -> None:
    # 6. user_badges
    with op.batch_alter_table("user_badges", schema=None) as batch:
        batch.add_column(sa.Column("revision_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("post_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("awarded_by_id", sa.Integer(), nullable=True))

    # 5. badges
    with op.batch_alter_table("badges", schema=None) as batch:
        batch.add_column(
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("points_value", sa.Integer(), nullable=True))

    # 4. revisions
    with op.batch_alter_table("revisions", schema=None) as batch:
        batch.drop_column("reviewed_at")
        batch.drop_column("base_version_number")
        batch.alter_column("rejection_note", new_column_name="reviewer_note")
        batch.alter_column("diff_cache", new_column_name="diff")

    # 3. post_versions
    with op.batch_alter_table("post_versions", schema=None) as batch:
        batch.drop_column("revision_id")
        batch.add_column(sa.Column("change_summary", sa.String(512), nullable=True))
        batch.add_column(sa.Column("author_id", sa.Integer(), nullable=True))

    # 2. notifications
    with op.batch_alter_table("notifications", schema=None) as batch:
        batch.drop_column("read_at")
        batch.drop_column("payload")
        batch.add_column(sa.Column("revision_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("post_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("actor_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("url", sa.String(512), nullable=True))
        batch.alter_column("notification_type", new_column_name="type")

    # 1. follows
    with op.batch_alter_table("follows", schema=None) as batch:
        batch.alter_column("followed_id", new_column_name="following_id")
