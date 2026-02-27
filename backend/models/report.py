"""Report model — user-submitted moderation reports on posts and comments."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Report(db.Model):
    """A report submitted by a user about a post or comment.

    Uniqueness rule
    ---------------
    A user may only have *one open* report per target at a time.  This is
    enforced at the service layer (not by a DB unique constraint) so that
    a user can re-report the same target after an earlier report has been
    resolved or dismissed.
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    reporter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 'post' or 'comment'
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)

    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 'open', 'resolved', 'dismissed'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolved_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    reporter: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[reporter_id]
    )
    resolver: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[resolved_by_id]
    )

    __table_args__ = (
        Index("ix_reports_target", "target_type", "target_id"),
        Index("ix_reports_status", "status"),
    )
