"""Sitemap service — builds the list of URL entries for ``/sitemap.xml``.

Each entry is a dict with:
- ``loc``     : absolute URL (str)
- ``lastmod`` : ISO-8601 date string ``YYYY-MM-DD`` (optional, str or None)
- ``changefreq`` : RFC value (optional, str or None)
- ``priority``   : float string "0.5" etc. (optional, str or None)

No draft, private, or admin-only URLs are ever included.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from flask import url_for
from sqlalchemy import func, select

from backend.extensions import db
from backend.models.portal import ProfileVisibility, UserPrivacySettings
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.tag import PostTag, Tag
from backend.models.user import User
from backend.utils.seo import absolute_url

# Stable epoch used when there are no published posts.
_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def _fmt_date(dt: datetime | None) -> str | None:
    """Format a datetime to ``YYYY-MM-DD`` (UTC) for ``<lastmod>``."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%d")


def _entry(
    loc: str,
    lastmod: datetime | None = None,
    changefreq: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    return {
        "loc": loc,
        "lastmod": _fmt_date(lastmod),
        "changefreq": changefreq,
        "priority": priority,
    }


def _compute_site_last_modified(post_ids: list[int]) -> datetime:
    """Return the most recent change timestamp across all published posts.

    Considers Post.updated_at, Post.published_at, and PostVersion.created_at.
    Falls back to _EPOCH when there are no published posts.
    Only published post IDs are passed in — drafts never affect the sitemap's
    ETag or Last-Modified.
    """
    if not post_ids:
        return _EPOCH

    row = db.session.execute(
        select(
            func.max(Post.updated_at).label("max_updated"),
            func.max(Post.published_at).label("max_published"),
        ).where(Post.id.in_(post_ids))
    ).one()

    version_max: datetime | None = db.session.scalar(
        select(func.max(PostVersion.created_at)).where(
            PostVersion.post_id.in_(post_ids)
        )
    )

    candidates: list[datetime] = []
    for ts in (row.max_updated, row.max_published, version_max):
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        candidates.append(ts)

    return max(candidates) if candidates else _EPOCH


def build_entries() -> tuple[list[dict[str, Any]], datetime]:
    """Return ``(entries, last_modified)`` for ``/sitemap.xml``.

    ``last_modified`` is the site-wide most recent change timestamp,
    derived from published posts, their ``updated_at``/``published_at``,
    and the latest accepted ``PostVersion.created_at``.

    Order: static pages → posts → tag pages → author profiles.
    """
    entries: list[dict[str, Any]] = []

    # ── Static / well-known pages ────────────────────────────────────────
    entries.append(
        _entry(absolute_url(url_for("index.index")), changefreq="daily", priority="1.0")
    )
    entries.append(
        _entry(
            absolute_url(url_for("posts.list_posts")),
            changefreq="daily",
            priority="0.9",
        )
    )
    entries.append(
        _entry(
            absolute_url(url_for("tags.tag_index")), changefreq="weekly", priority="0.6"
        )
    )
    entries.append(
        _entry(
            absolute_url(url_for("search.search_results")),
            changefreq="monthly",
            priority="0.4",
        )
    )

    # ── Published posts ──────────────────────────────────────────────────
    posts: list[Post] = list(
        db.session.scalars(
            select(Post)
            .where(Post.status == PostStatus.published)
            .order_by(Post.published_at.desc(), Post.id.desc())
        )
    )
    # Compute site-wide last_modified now (only published post IDs are used).
    post_ids = [p.id for p in posts]
    site_last_modified = _compute_site_last_modified(post_ids)

    for post in posts:
        # Use the most recent of published_at / updated_at as lastmod.
        candidates = [t for t in (post.published_at, post.updated_at) if t is not None]
        lastmod = max(candidates) if candidates else None
        entries.append(
            _entry(
                loc=absolute_url(url_for("posts.post_detail", slug=post.slug)),
                lastmod=lastmod,
                changefreq="monthly",
                priority="0.8",
            )
        )

    # ── Tag pages (only tags with ≥1 published post) ─────────────────────
    # Tags index already covered; here we add filtered post-list URLs.
    tag_slugs_with_posts: list[str] = [
        row[0]
        for row in db.session.execute(
            select(Tag.slug)
            .join(PostTag, PostTag.c.tag_id == Tag.id)
            .join(
                Post,
                (Post.id == PostTag.c.post_id) & (Post.status == PostStatus.published),
            )
            .distinct()
        ).all()
    ]
    for slug in sorted(tag_slugs_with_posts):
        entries.append(
            _entry(
                loc=absolute_url(url_for("posts.list_posts", tag=slug)),
                changefreq="weekly",
                priority="0.6",
            )
        )

    # ── Public author profiles ────────────────────────────────────────────
    # Include users whose profile is public or members-only (not private).
    # Only active users with at least one published post.
    private_user_ids: list[int] = [
        row[0]
        for row in db.session.execute(
            select(UserPrivacySettings.user_id).where(
                UserPrivacySettings.profile_visibility == ProfileVisibility.private
            )
        ).all()
    ]

    active_authors = list(
        db.session.scalars(
            select(User)
            .where(
                User.is_active.is_(True),
                User.id.notin_(private_user_ids) if private_user_ids else True,
            )
            .join(
                Post,
                (Post.author_id == User.id) & (Post.status == PostStatus.published),
            )
            .distinct()
        )
    )
    for user in active_authors:
        entries.append(
            _entry(
                loc=absolute_url(url_for("users.profile", username=user.username)),
                changefreq="weekly",
                priority="0.5",
            )
        )

    return entries, site_last_modified
