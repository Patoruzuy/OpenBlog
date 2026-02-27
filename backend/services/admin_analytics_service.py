"""Admin analytics service — aggregates for the analytics dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.analytics import AnalyticsEvent
from backend.models.comment import Comment
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.tag import Tag
from backend.models.user import User


class AdminAnalyticsService:
    @staticmethod
    def overview(days: int = 30) -> dict:
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        # ── Traffic ────────────────────────────────────────────────────────

        # Total page-view events in period
        total_views = db.session.scalar(
            select(func.count(AnalyticsEvent.id))
            .where(AnalyticsEvent.event_type == "post_view")
            .where(AnalyticsEvent.occurred_at >= since)
        ) or 0

        # Top posts by views (all time — reflects long-term performance)
        top_posts = list(
            db.session.scalars(
                select(Post)
                .where(Post.status == PostStatus.published)
                .options(joinedload(Post.author))
                .order_by(Post.view_count.desc())
                .limit(10)
            ).all()
        )

        # Page views trend (per day in window)
        pv_by_day = db.session.execute(
            select(
                func.date(AnalyticsEvent.occurred_at).label("day"),
                func.count(AnalyticsEvent.id).label("views"),
            )
            .where(AnalyticsEvent.event_type == "post_view")
            .where(AnalyticsEvent.occurred_at >= since)
            .group_by(func.date(AnalyticsEvent.occurred_at))
            .order_by(func.date(AnalyticsEvent.occurred_at))
        ).all()

        # ── Revisions ──────────────────────────────────────────────────────

        # All-time funnel (absolute totals used in the all-time overview)
        rev_funnel_all = {
            row[0]: row[1]
            for row in db.session.execute(
                select(Revision.status, func.count(Revision.id)).group_by(Revision.status)
            ).all()
        }

        # Period-windowed funnel:
        #   pending  — submitted in window (regardless of review outcome)
        #   accepted — reviewed (accepted) in window
        #   rejected — reviewed (rejected) in window
        rev_submitted_period = db.session.scalar(
            select(func.count(Revision.id))
            .where(Revision.created_at >= since)
        ) or 0
        rev_accepted_period = db.session.scalar(
            select(func.count(Revision.id))
            .where(Revision.status == RevisionStatus.accepted)
            .where(Revision.reviewed_at >= since)
        ) or 0
        rev_rejected_period = db.session.scalar(
            select(func.count(Revision.id))
            .where(Revision.status == RevisionStatus.rejected)
            .where(Revision.reviewed_at >= since)
        ) or 0

        # Average review latency (Python-side to stay SQLite-compatible)
        reviewed_pairs = db.session.execute(
            select(Revision.created_at, Revision.reviewed_at)
            .where(Revision.status.in_([RevisionStatus.accepted, RevisionStatus.rejected]))
            .where(Revision.reviewed_at.isnot(None))
            .where(Revision.reviewed_at >= since)
        ).all()
        if reviewed_pairs:
            avg_review_days: float | None = round(
                sum(
                    (r.reviewed_at - r.created_at).total_seconds() / 86400
                    for r in reviewed_pairs
                )
                / len(reviewed_pairs),
                1,
            )
        else:
            avg_review_days = None

        # Acceptance rate for this period
        rev_reviewed_period = rev_accepted_period + rev_rejected_period
        acceptance_rate: float | None = (
            round(rev_accepted_period / rev_reviewed_period * 100, 1)
            if rev_reviewed_period > 0
            else None
        )

        # ── Contributors ───────────────────────────────────────────────────

        # Active contributors (submitted ≥1 revision in window)
        active_contributors = db.session.scalar(
            select(func.count(func.distinct(Revision.author_id)))
            .where(Revision.created_at >= since)
        ) or 0

        # Accepted contributions in window (for stat card)
        accepted_contribs = db.session.scalar(
            select(func.count(Revision.id))
            .where(Revision.status == RevisionStatus.accepted)
            .where(Revision.reviewed_at >= since)
        ) or 0

        # First-time contributors: authors whose very first revision is in this window
        author_first = (
            select(Revision.author_id, func.min(Revision.created_at).label("first_at"))
            .group_by(Revision.author_id)
            .subquery()
        )
        first_time_contributors = db.session.scalar(
            select(func.count()).select_from(author_first).where(author_first.c.first_at >= since)
        ) or 0

        # Top contributors by accepted revisions in window (internal view; no emails exposed)
        top_contrib_rows = db.session.execute(
            select(
                User.username,
                User.display_name,
                func.count(Revision.id).label("accepted_count"),
            )
            .join(User, User.id == Revision.author_id)
            .where(Revision.status == RevisionStatus.accepted)
            .where(Revision.reviewed_at >= since)
            .group_by(User.id, User.username, User.display_name)
            .order_by(desc("accepted_count"))
            .limit(5)
        ).all()
        top_contributors = [
            {
                "username": r.username,
                "display": r.display_name or r.username,
                "count": r.accepted_count,
            }
            for r in top_contrib_rows
        ]

        # ── Users ──────────────────────────────────────────────────────────

        # User signups per day in window
        signups_by_day = db.session.execute(
            select(
                func.date(User.created_at).label("day"),
                func.count(User.id).label("users"),
            )
            .where(User.created_at >= since)
            .group_by(func.date(User.created_at))
            .order_by(func.date(User.created_at))
        ).all()

        # ── Topics ─────────────────────────────────────────────────────────

        # Top tags by post count
        top_tags = db.session.execute(
            select(Tag.name, Tag.slug, func.count(Post.id).label("post_count"))
            .join(Post.tags)
            .where(Post.status == PostStatus.published)
            .group_by(Tag.id, Tag.name, Tag.slug)
            .order_by(desc("post_count"))
            .limit(10)
        ).all()

        # ── Content freshness ──────────────────────────────────────────────

        stale_threshold = now - timedelta(days=90)
        stale_posts = list(
            db.session.scalars(
                select(Post)
                .where(Post.status == PostStatus.published)
                .where(Post.updated_at < stale_threshold)
                .order_by(Post.updated_at.asc())
                .limit(8)
            ).all()
        )

        # Published posts with zero or near-zero views
        low_traffic_posts = list(
            db.session.scalars(
                select(Post)
                .where(Post.status == PostStatus.published)
                .where(Post.view_count < 10)
                .order_by(Post.published_at.desc())
                .limit(8)
            ).all()
        )

        # ── Comments ───────────────────────────────────────────────────────

        comments_by_day = db.session.execute(
            select(
                func.date(Comment.created_at).label("day"),
                func.count(Comment.id).label("comments"),
            )
            .where(Comment.created_at >= since)
            .group_by(func.date(Comment.created_at))
            .order_by(func.date(Comment.created_at))
        ).all()

        return {
            # traffic
            "total_views":   total_views,
            "top_posts":     top_posts,
            "pv_by_day":     [{"date": str(r.day), "views": r.views} for r in pv_by_day],
            # revisions — all-time funnel kept for reference
            "rev_funnel": {
                "pending":  rev_funnel_all.get(RevisionStatus.pending, 0),
                "accepted": rev_funnel_all.get(RevisionStatus.accepted, 0),
                "rejected": rev_funnel_all.get(RevisionStatus.rejected, 0),
            },
            # revisions — period-windowed
            "rev_submitted_period": rev_submitted_period,
            "rev_accepted_period":  rev_accepted_period,
            "rev_rejected_period":  rev_rejected_period,
            "avg_review_days":      avg_review_days,
            "acceptance_rate":      acceptance_rate,
            # contributors
            "active_contributors":    active_contributors,
            "accepted_contribs":      accepted_contribs,
            "first_time_contributors": first_time_contributors,
            "top_contributors":        top_contributors,
            # users
            "signups_by_day": [{"date": str(r.day), "users": r.users} for r in signups_by_day],
            # topics
            "top_tags": [
                {"name": r.name, "slug": r.slug, "count": r.post_count}
                for r in top_tags
            ],
            # content freshness
            "stale_posts":       stale_posts,
            "low_traffic_posts": low_traffic_posts,
            # comments
            "comments_by_day": [
                {"date": str(r.day), "comments": r.comments}
                for r in comments_by_day
            ],
            "days": days,
        }
