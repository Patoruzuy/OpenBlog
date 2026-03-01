"""Admin dashboard service — fast aggregate queries for the control center."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from backend.extensions import db
from backend.models.admin import AuditLog
from backend.models.analytics import AnalyticsEvent
from backend.models.comment import Comment
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User


class AdminDashboardService:
    """Aggregates all dashboard widgets in one round-trip-minimal call."""

    @staticmethod
    def get_snapshot() -> dict:
        """Return a dict of all dashboard widget data."""
        now = datetime.now(UTC)
        since_7d = now - timedelta(days=7)
        since_30d = now - timedelta(days=30)

        # ── Post counts ────────────────────────────────────────────────────
        post_counts = {
            row[0]: row[1]
            for row in db.session.execute(
                select(Post.status, func.count(Post.id)).group_by(Post.status)
            ).all()
        }

        # ── Revision counts ────────────────────────────────────────────────
        rev_counts = {
            row[0]: row[1]
            for row in db.session.execute(
                select(Revision.status, func.count(Revision.id)).group_by(
                    Revision.status
                )
            ).all()
        }

        # ── Recent revisions (last 7d) ─────────────────────────────────────
        recent_rev_counts = {
            row[0]: row[1]
            for row in db.session.execute(
                select(Revision.status, func.count(Revision.id))
                .where(Revision.created_at >= since_7d)
                .group_by(Revision.status)
            ).all()
        }

        # ── User counts ────────────────────────────────────────────────────
        total_users = db.session.scalar(select(func.count(User.id))) or 0
        unverified_users = (
            db.session.scalar(
                select(func.count(User.id)).where(User.is_email_verified == False)  # noqa: E712
            )
            or 0
        )
        inactive_users = (
            db.session.scalar(
                select(func.count(User.id)).where(User.is_active == False)  # noqa: E712
            )
            or 0
        )
        new_users_7d = (
            db.session.scalar(
                select(func.count(User.id)).where(User.created_at >= since_7d)
            )
            or 0
        )
        new_users_30d = (
            db.session.scalar(
                select(func.count(User.id)).where(User.created_at >= since_30d)
            )
            or 0
        )

        # ── Comment moderation ─────────────────────────────────────────────
        flagged_comments = (
            db.session.scalar(
                select(func.count(Comment.id))
                .where(Comment.is_flagged == True)  # noqa: E712
                .where(Comment.is_deleted == False)  # noqa: E712
            )
            or 0
        )
        total_comments_7d = (
            db.session.scalar(
                select(func.count(Comment.id)).where(Comment.created_at >= since_7d)
            )
            or 0
        )

        # ── Recent audit events ────────────────────────────────────────────
        recent_audit = list(
            db.session.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
            ).all()
        )
        for entry in recent_audit:
            db.session.refresh(entry)  # eager-load actor

        # ── Top posts (30d) ────────────────────────────────────────────────
        top_posts = list(
            db.session.scalars(
                select(Post)
                .where(Post.status == PostStatus.published)
                .order_by(Post.view_count.desc())
                .limit(5)
            ).all()
        )

        # ── Analytics: page-view trend (7d) ───────────────────────────────
        pv_trend = _daily_view_counts(since_7d, now)

        # ── Active contributors (30d) ──────────────────────────────────────
        active_contributors_30d = (
            db.session.scalar(
                select(func.count(func.distinct(Revision.author_id))).where(
                    Revision.created_at >= since_30d
                )
            )
            or 0
        )

        # ── System health (lightweight, best-effort) ───────────────────────
        from backend.services.system_health_service import (
            SystemHealthService,  # noqa: PLC0415
        )

        try:
            health = SystemHealthService.get_status()
        except Exception as exc:
            import logging  # noqa: PLC0415

            logging.getLogger(__name__).warning(
                "SystemHealthService.get_status() failed: %s", exc, exc_info=True
            )
            health = {
                "db": {"ok": False, "label": "?"},
                "redis": {"ok": False, "label": "?"},
                "celery": {"ok": False, "label": "?"},
            }

        return {
            # posts
            "posts_published": post_counts.get(PostStatus.published, 0),
            "posts_draft": post_counts.get(PostStatus.draft, 0),
            "posts_scheduled": post_counts.get(PostStatus.scheduled, 0),
            "posts_archived": post_counts.get(PostStatus.archived, 0),
            # revisions
            "revisions_pending": rev_counts.get(RevisionStatus.pending, 0),
            "revisions_accepted": rev_counts.get(RevisionStatus.accepted, 0),
            "revisions_rejected": rev_counts.get(RevisionStatus.rejected, 0),
            "revisions_recent_pending": recent_rev_counts.get(
                RevisionStatus.pending, 0
            ),
            "revisions_recent_accepted": recent_rev_counts.get(
                RevisionStatus.accepted, 0
            ),
            # users
            "total_users": total_users,
            "unverified_users": unverified_users,
            "inactive_users": inactive_users,
            "new_users_7d": new_users_7d,
            "new_users_30d": new_users_30d,
            # moderation
            "flagged_comments": flagged_comments,
            "comments_7d": total_comments_7d,
            # contributors
            "active_contributors_30d": active_contributors_30d,
            # activity
            "recent_audit": recent_audit,
            "top_posts": top_posts,
            "pv_trend": pv_trend,
            # system
            "health": health,
        }


def _daily_view_counts(since: datetime, until: datetime) -> list[dict]:
    """Return [{"date": "YYYY-MM-DD", "views": n}, …] for the window."""
    rows = db.session.execute(
        select(
            func.date(AnalyticsEvent.occurred_at).label("day"),
            func.count(AnalyticsEvent.id).label("views"),
        )
        .where(AnalyticsEvent.event_type == "post_view")
        .where(AnalyticsEvent.occurred_at >= since)
        .where(AnalyticsEvent.occurred_at <= until)
        .group_by(func.date(AnalyticsEvent.occurred_at))
        .order_by(func.date(AnalyticsEvent.occurred_at))
    ).all()
    return [{"date": str(row.day), "views": row.views} for row in rows]
