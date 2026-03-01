"""Merge migration: join a9c2e7f4b831 and b1d4f6c8e295.

Resolves the two-headed migration graph so that ``alembic upgrade head``
(singular) works on any deployment, regardless of which branch(es) have
been applied.

Background
----------
There are two parallel branches descending from f3a7d9e2b451:

  Branch 1: … → f3a7d9e2b451 → a9c2e7f4b831
  Branch 2: … → f3a7d9e2b451 → a1b3c5d7e9f2 → b2c4e6f8a0d1
                → c1d3e5f7a9b2 → f5a8d2c6e047 → a8e2f4c6d031
                → b1d4f6c8e295

Environments that only applied branch 1 are missing the columns added in
c1d3e5f7a9b2 (notify_thread_emails, notify_reply_emails on
user_privacy_settings) and all subsequent branch-2 migrations.

Running ``alembic upgrade head`` after this revision is applied will first
catch up branch 2 (applying a1b3c5d7e9f2 through b1d4f6c8e295) and then
apply this merge point, leaving a single head: d3e6f0b2c5a8.

Revision ID: d3e6f0b2c5a8
Revises:     a9c2e7f4b831, b1d4f6c8e295
Create Date: 2026-02-28 00:00:00.000000
"""

from __future__ import annotations

revision = "d3e6f0b2c5a8"
# Chain is now linear: a9c2e7f4b831 → a1b3c5d7e9f2 → … → b1d4f6c8e295.
# This revision is kept as a stable waypoint (referenced by e4f5a6b7c8d9)
# but is no longer a true merge point.
down_revision = "b1d4f6c8e295"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge point only — all schema changes are in the individual branch migrations.
    pass


def downgrade() -> None:
    pass
