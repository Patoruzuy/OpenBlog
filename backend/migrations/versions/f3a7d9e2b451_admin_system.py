"""admin system: audit_logs and site_settings tables

Revision ID: f3a7d9e2b451
Revises: e1b5f3c7d920
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "f3a7d9e2b451"
down_revision = "e1b5f3c7d920"
branch_labels = None
depends_on = None


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ── audit_logs ────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("target_repr", sa.String(512), nullable=True),
        sa.Column(
            "before_state",
            sa.JSON() if _is_pg() else sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "after_state",
            sa.JSON() if _is_pg() else sa.Text(),
            nullable=True,
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_audit_logs_actor_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_actor_at",
        "audit_logs",
        ["actor_id", "created_at"],
    )
    op.create_index(
        "ix_audit_target",
        "audit_logs",
        ["target_type", "target_id"],
    )
    op.create_index(
        "ix_audit_action_at",
        "audit_logs",
        ["action", "created_at"],
    )

    # ── site_settings ─────────────────────────────────────────────────────
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column(
            "value",
            sa.JSON() if _is_pg() else sa.Text(),
            nullable=True,
        ),
        sa.Column("group", sa.String(64), nullable=True),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            onupdate=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name="fk_site_settings_updated_by_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_site_settings_key"),
    )
    op.create_index("ix_site_settings_key", "site_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_site_settings_key", table_name="site_settings")
    op.drop_table("site_settings")

    op.drop_index("ix_audit_action_at", table_name="audit_logs")
    op.drop_index("ix_audit_target", table_name="audit_logs")
    op.drop_index("ix_audit_actor_at", table_name="audit_logs")
    op.drop_table("audit_logs")
