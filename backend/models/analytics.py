"""AnalyticsEvent model — raw page-view and engagement events.

Events are written in batches by a Celery task (Phase 8) that flushes a Redis
counter queue to the DB.  This table is append-only; no updates or deletes.

For production reporting, a materialized view or a separate OLAP store
(e.g. ClickHouse) should aggregate these rows.  For Phase 1–8, direct queries
on this table are sufficient.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.extensions import db


class AnalyticsEvent(db.Model):
    """A single analytics event (page view, post click, etc.)."""

    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Event type key, e.g. 'page_view', 'post_view', 'search'
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Optional FK to a post (nullable — some events are not post-specific)
    post_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Optional FK to a user (nullable — anonymous views are expected)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Session / request metadata (anonymised — no PII stored)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA-256 prefix of User-Agent string (first 16 chars). No raw UA stored.",
    )
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_analytics_post_type_time", "post_id", "event_type", "occurred_at"),
        Index("ix_analytics_event_time", "event_type", "occurred_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AnalyticsEvent id={self.id} type={self.event_type!r} "
            f"post_id={self.post_id} at={self.occurred_at.isoformat()}>"
        )
