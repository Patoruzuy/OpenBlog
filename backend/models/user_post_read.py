"""UserPostRead model — per-user reading history for posts.

Tracks the last time a user visited a post and which version they read.
Used to show "Updated since your last visit" indicators on the post detail
and list pages.

Privacy notes
-------------
- Only created for authenticated users; anonymous visits are not tracked.
- The record is upserted (not appended) so there is at most one row per
  (user, post) pair — no browsing history accumulates.
- ``last_read_version`` is the ``Post.version`` value at the time of the
  most-recent visit, enabling a cheap "has the post changed?" comparison.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class UserPostRead(db.Model):
    """Single row per (user, post) pair recording the last read event."""

    __tablename__ = "user_post_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Foreign keys ───────────────────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Read state ─────────────────────────────────────────────────────────
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="Wall-clock time of the most recent visit.",
    )
    last_read_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="Post.version at the time of the most recent visit.",
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[user_id]
    )
    post: Mapped[Post] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Post", foreign_keys=[post_id]
    )

    # ── Constraints ────────────────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("user_id", "post_id", name="uq_user_post_reads_user_post"),
    )

    def __repr__(self) -> str:
        return (
            f"<UserPostRead user_id={self.user_id} post_id={self.post_id} "
            f"v={self.last_read_version}>"
        )
