"""notifications: add composite index (user_id, is_read, created_at)

Revision ID: a1b3c5d7e9f2
Revises: f3a7d9e2b451
Create Date: 2025-01-02 00:00:00.000000
"""

from alembic import op

revision = "a1b3c5d7e9f2"
down_revision = "f3a7d9e2b451"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite index to accelerate the common query pattern:
    #   WHERE user_id = ? AND is_read = false ORDER BY created_at DESC
    op.create_index(
        "ix_notifications_user_is_read_created",
        "notifications",
        ["user_id", "is_read", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_is_read_created", table_name="notifications")
