"""Analytics service — event recording, queuing, and aggregation.

Two write paths
---------------
``record_event()``
    Synchronous, direct DB insert.  Use for low-volume paths (e.g. direct API
    calls in tests) or when the Redis queue is not available.

``queue_event()``
    Pushes a JSON blob to a Redis list (``analytics:event_queue``).  The Celery
    task ``flush_analytics_queue`` drains this list in batches and bulk-inserts
    to the DB.  Preferred for high-traffic paths (e.g. SSR page views) where
    the added DB write latency would degrade response times.

Read path
---------
``get_post_stats(post_id)``
    Aggregates events for a single post — total views, unique sessions, views
    in the last 30 days, and the top 5 referrers.

``get_top_posts(limit, days)``
    Cross-post ranking by ``post_view`` count within the last *days* days.

Privacy notes
-------------
- No raw User-Agent strings are stored; only the first 16 hex chars of the
  SHA-256 hash (sufficient for deduplication, not reversible).
- IP addresses are never recorded.
- ``session_id`` is treated as an opaque token; it is never linked back to an
  authenticated user in this service.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, distinct, func, select

from backend.extensions import db
from backend.models.analytics import AnalyticsEvent

#: Redis list key used as the event buffer.
_QUEUE_KEY = "analytics:event_queue"


class AnalyticsService:
    """Static-method service for analytics event handling."""

    # ── Write paths ───────────────────────────────────────────────────────────

    @staticmethod
    def record_event(
        event_type: str,
        *,
        post_id: int | None = None,
        user_id: int | None = None,
        session_id: str | None = None,
        referrer: str | None = None,
        user_agent: str | None = None,
        country_code: str | None = None,
    ) -> AnalyticsEvent:
        """Write an analytics event directly to the DB (synchronous).

        Parameters
        ----------
        event_type:
            Stable string key, e.g. ``"post_view"``, ``"search"``.
        user_agent:
            Raw User-Agent string — hashed before storage; original is never
            persisted.
        """
        ua_hash: str | None = None
        if user_agent:
            ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:16]

        event = AnalyticsEvent(
            event_type=event_type,
            post_id=post_id,
            user_id=user_id,
            session_id=session_id,
            referrer=referrer[:512] if referrer else None,
            user_agent_hash=ua_hash,
            country_code=country_code,
        )
        db.session.add(event)
        db.session.commit()
        return event

    @staticmethod
    def queue_event(
        event_type: str,
        *,
        post_id: int | None = None,
        user_id: int | None = None,
        session_id: str | None = None,
        referrer: str | None = None,
        user_agent: str | None = None,
        country_code: str | None = None,
    ) -> None:
        """Push an analytics event onto the Redis buffer (non-blocking).

        Silently swallows any Redis errors so a Redis blip never breaks a page
        render.  The Celery task ``flush_analytics_queue`` drains the buffer.
        """
        from flask import current_app

        ua_hash: str | None = None
        if user_agent:
            ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:16]

        payload: dict[str, Any] = {
            "event_type": event_type,
            "post_id": post_id,
            "user_id": user_id,
            "session_id": session_id,
            "referrer": referrer[:512] if referrer else None,
            "user_agent_hash": ua_hash,
            "country_code": country_code,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        try:
            redis = current_app.extensions["redis"]
            redis.rpush(_QUEUE_KEY, json.dumps(payload))
        except Exception:  # noqa: BLE001
            # Best-effort — analytics loss is preferable to a 500 error.
            pass

    #: Maximum events processed per flush batch to bound memory usage.
    _FLUSH_BATCH_SIZE: int = 500

    @staticmethod
    def flush_queued_events() -> int:
        """Drain the Redis event queue and bulk-insert to the DB in batches.

        Returns the total number of events written.  Designed to be called
        from the Celery task ``flush_analytics_queue``.

        The operation is *at-most-once* per batch: each batch is removed from
        Redis before being committed to the DB so a crash loses that batch
        rather than double-counting.  For analytics, occasional loss is
        acceptable; double-counting is worse.

        Batching bounds memory usage: at most ``_FLUSH_BATCH_SIZE`` records
        are held in Python memory at once.
        """
        from flask import current_app

        redis = current_app.extensions["redis"]
        total_written = 0

        while True:
            # Read the next batch from the head of the queue.
            raw_items: list[str] = redis.lrange(
                _QUEUE_KEY, 0, AnalyticsService._FLUSH_BATCH_SIZE - 1
            )
            if not raw_items:
                break

            # Remove exactly the items we just read before committing so that
            # new events pushed during this flush are not lost.
            redis.ltrim(_QUEUE_KEY, len(raw_items), -1)

            events: list[AnalyticsEvent] = []
            for raw in raw_items:
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    continue

                occurred_raw = data.get("occurred_at")
                occurred_at = (
                    datetime.fromisoformat(occurred_raw)
                    if occurred_raw
                    else datetime.now(UTC)
                )

                events.append(
                    AnalyticsEvent(
                        event_type=data.get("event_type", "unknown"),
                        post_id=data.get("post_id"),
                        user_id=data.get("user_id"),
                        session_id=data.get("session_id"),
                        referrer=data.get("referrer"),
                        user_agent_hash=data.get("user_agent_hash"),
                        country_code=data.get("country_code"),
                        occurred_at=occurred_at,
                    )
                )

            if events:
                db.session.add_all(events)
                db.session.commit()

            total_written += len(events)

        return total_written

    # ── Read paths ────────────────────────────────────────────────────────────

    @staticmethod
    def get_post_stats(post_id: int) -> dict:
        """Aggregate analytics for a single post.

        Returns
        -------
        dict with keys:
            post_id, total_events, views, unique_sessions,
            views_last_30_days, top_referrers
        """
        from backend.models.post import Post

        post = db.session.get(Post, post_id)

        base_q = select(AnalyticsEvent).where(
            AnalyticsEvent.post_id == post_id
        )

        total_events: int = (
            db.session.scalar(
                select(func.count()).select_from(base_q.subquery())
            )
            or 0
        )

        views: int = (
            db.session.scalar(
                select(func.count()).where(
                    AnalyticsEvent.post_id == post_id,
                    AnalyticsEvent.event_type == "post_view",
                )
            )
            or 0
        )

        unique_sessions: int = (
            db.session.scalar(
                select(func.count(distinct(AnalyticsEvent.session_id))).where(
                    AnalyticsEvent.post_id == post_id,
                    AnalyticsEvent.session_id.is_not(None),
                )
            )
            or 0
        )

        cutoff_30 = datetime.now(UTC) - timedelta(days=30)
        views_last_30: int = (
            db.session.scalar(
                select(func.count()).where(
                    AnalyticsEvent.post_id == post_id,
                    AnalyticsEvent.event_type == "post_view",
                    AnalyticsEvent.occurred_at >= cutoff_30,
                )
            )
            or 0
        )

        # Top 5 referrers (non-null).
        referrer_rows = db.session.execute(
            select(
                AnalyticsEvent.referrer,
                func.count(AnalyticsEvent.id).label("count"),
            )
            .where(
                AnalyticsEvent.post_id == post_id,
                AnalyticsEvent.referrer.is_not(None),
            )
            .group_by(AnalyticsEvent.referrer)
            .order_by(desc("count"))
            .limit(5)
        ).all()

        return {
            "post_id": post_id,
            "slug": post.slug if post else None,
            "total_events": total_events,
            "views": views,
            "unique_sessions": unique_sessions,
            "views_last_30_days": views_last_30,
            "top_referrers": [
                {"referrer": row.referrer, "count": row.count}
                for row in referrer_rows
            ],
        }

    @staticmethod
    def get_top_posts(limit: int = 10, days: int = 30) -> list[dict]:
        """Return the top *limit* posts by ``post_view`` count in the last *days* days.

        Each item: ``{post_id, slug, title, view_count}``
        """
        from backend.models.post import Post

        cutoff = datetime.now(UTC) - timedelta(days=days)

        rows = db.session.execute(
            select(
                AnalyticsEvent.post_id,
                func.count(AnalyticsEvent.id).label("view_count"),
            )
            .where(
                AnalyticsEvent.event_type == "post_view",
                AnalyticsEvent.post_id.is_not(None),
                AnalyticsEvent.occurred_at >= cutoff,
            )
            .group_by(AnalyticsEvent.post_id)
            .order_by(desc("view_count"))
            .limit(limit)
        ).all()

        results: list[dict] = []
        for row in rows:
            post = db.session.get(Post, row.post_id)
            results.append(
                {
                    "post_id": row.post_id,
                    "slug": post.slug if post else None,
                    "title": post.title if post else None,
                    "view_count": row.view_count,
                }
            )
        return results
