"""thread notification preference columns on user_privacy_settings

Revision ID: c1d3e5f7a9b2
Revises: b2c4e6f8a0d1
Create Date: 2026-03-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c1d3e5f7a9b2"
down_revision = "b2c4e6f8a0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_privacy_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "notify_thread_emails",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            )
        )
        batch_op.add_column(
            sa.Column(
                "notify_reply_emails",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_privacy_settings") as batch_op:
        batch_op.drop_column("notify_reply_emails")
        batch_op.drop_column("notify_thread_emails")
