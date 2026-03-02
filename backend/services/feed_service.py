"""Feed service — builds RSS channel + item dicts for the feed routes.

All three public feeds (global, tag, author) return *only* ``published``
posts; draft, scheduled, and archived posts are never exposed.

For the author feed an extra privacy check is applied:
- ``private`` profile → 404 (caller receives ``None``).
- ``members`` or ``public`` profile → feed is returned.

No email addresses are ever included in the returned data.

Return contract
---------------
Every public function returns a 3-tuple:
    ``(channel: dict, items: list[dict], last_modified: datetime)``

``last_modified`` is a timezone-aware UTC datetime reflecting the most
recently changed post among the included items.  It incorporates:
  - ``Post.updated_at`` (updated by ORM on any field change)
  - ``Post.published_at``
  - Latest ``PostVersion.created_at`` for each included post (a new
    PostVersion is created every time an accepted revision bumps the version)

If the result set is empty, last_modified falls back to a stable epoch so
callers always have a non-null value for ETag/Last-Modified computation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from flask import current_app, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.portal import ProfileVisibility
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.tag import Tag
from backend.models.user import User
from backend.utils.seo import absolute_url

# Stable epoch used as last_modified when there are no published posts.
_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)

# Characters kept when stripping Markdown to plain text for the <description>.
_MD_STRIP_RE = re.compile(
    r"```.*?```|`[^`]+`"  # fenced + inline code blocks
    r"|!\[.*?\]\(.*?\)"  # images
    r"|\[([^\]]*)\]\(.*?\)"  # links → keep display text
    r"|#{1,6}\s+"  # heading markers
    r"|[*_~]{1,3}"  # bold / italic / strikethrough markers
    r"|\n+",  # collapse newlines to a space
    flags=re.DOTALL,
)

_EXCERPT_LEN = 200  # max characters for the feed description


def _to_excerpt(markdown: str) -> str:
    """Return a plain-text excerpt (≤ _EXCERPT_LEN chars) from *markdown*."""

    def _repl(m: re.Match) -> str:  # type: ignore[type-arg]
        # Keep link display text (group 1), replace everything else with space.
        return (m.group(1) or " ") if m.group(1) is not None else " "

    plain = _MD_STRIP_RE.sub(_repl, markdown).strip()
    # Collapse multiple spaces that may result from substitutions.
    plain = re.sub(r" {2,}", " ", plain)
    if len(plain) > _EXCERPT_LEN:
        # Truncate at word boundary.
        plain = plain[:_EXCERPT_LEN].rsplit(" ", 1)[0] + "…"
    return plain


def _post_to_item(post: Post) -> dict[str, Any]:
    """Convert a *Post* ORM object to an RSS item dict.

    The ``link`` and ``guid`` values are absolute URLs built from
    ``PUBLIC_BASE_URL`` via :func:`~backend.utils.seo.absolute_url`.
    Routes prompts to /prompts/<slug> and articles to /posts/<slug>.
    """
    if post.kind == "prompt":
        post_url = absolute_url(url_for("prompts.public_prompt_detail", slug=post.slug))
    else:
        post_url = absolute_url(url_for("posts.post_detail", slug=post.slug))
    return {
        "title": post.title,
        "link": post_url,
        "guid": post_url,
        "description": post.seo_description or _to_excerpt(post.markdown_body),
        "published_at": post.published_at or post.updated_at,
        "author_name": post.author.display_name or post.author.username,
        "tags": [t.name for t in post.tags],
        "og_image_url": post.og_image_url,
    }


def _channel(title: str, description: str, link: str, feed_url: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "link": link,
        "feed_url": feed_url,
        "language": "en",
        "last_build_date": datetime.now(UTC),
    }


def _published_query(limit: int):
    """Base query: published posts, author + tags eager-loaded, newest first.

    INV-001: only public (workspace_id IS NULL), published, with a published_at
    timestamp — workspace docs are never exposed in public feeds.
    Includes kind='article' and kind='prompt'; playbooks/frameworks excluded.
    """
    return (
        select(Post)
        .where(
            Post.workspace_id.is_(None),
            Post.kind.in_(["article", "prompt"]),
            Post.status == PostStatus.published,
            Post.published_at.is_not(None),
        )
        .options(joinedload(Post.author), joinedload(Post.tags))
        .order_by(Post.published_at.desc(), Post.id.desc())
        .limit(limit)
    )


def _compute_last_modified(post_ids: list[int]) -> datetime:
    """Return the most recent change timestamp across *post_ids*.

    Considers Post.updated_at, Post.published_at, and PostVersion.created_at
    (which is inserted whenever an accepted revision bumps the post version).

    Falls back to _EPOCH when *post_ids* is empty so callers always receive
    a non-null datetime.  Only published posts are ever passed in, so this
    function cannot inadvertently expose draft timestamps.
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


# ── Public API ────────────────────────────────────────────────────────────────


def get_global_feed(
    limit: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime]:
    """Return ``(channel, items, last_modified)`` for the site-wide feed."""
    site_name: str = current_app.config.get("SITE_NAME", "OpenBlog")
    site_link = absolute_url("/")
    feed_url = absolute_url(url_for("feed.global_feed"))

    posts = list(db.session.scalars(_published_query(limit)).unique())
    post_ids = [p.id for p in posts]
    last_modified = _compute_last_modified(post_ids)

    channel = _channel(
        title=site_name,
        description=f"Latest posts from {site_name}",
        link=site_link,
        feed_url=feed_url,
    )
    return channel, [_post_to_item(p) for p in posts], last_modified


def get_tag_feed(
    slug: str,
    limit: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime] | None:
    """Return ``(channel, items, last_modified)`` for a tag feed.

    Returns ``None`` if the tag does not exist (caller should 404).
    """
    tag: Tag | None = db.session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is None:
        return None

    site_name: str = current_app.config.get("SITE_NAME", "OpenBlog")
    tag_link = absolute_url(url_for("posts.list_posts", tag=slug))
    feed_url = absolute_url(url_for("feed.tag_feed", slug=slug))

    posts = list(
        db.session.scalars(
            select(Post)
            .where(
                Post.workspace_id.is_(None),
                Post.status == PostStatus.published,
                Post.published_at.is_not(None),
                Post.tags.any(Tag.slug == slug),
            )
            .options(joinedload(Post.author), joinedload(Post.tags))
            .order_by(Post.published_at.desc(), Post.id.desc())
            .limit(limit)
        ).unique()
    )

    post_ids = [p.id for p in posts]
    last_modified = _compute_last_modified(post_ids)

    channel = _channel(
        title=f"#{tag.name} — {site_name}",
        description=tag.description or f"Posts tagged #{tag.name} on {site_name}",
        link=tag_link,
        feed_url=feed_url,
    )
    return channel, [_post_to_item(p) for p in posts], last_modified


def get_author_feed(
    username: str,
    limit: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime] | None:
    """Return ``(channel, items, last_modified)`` for an author feed.

    Returns ``None`` if:
    - the user does not exist, or
    - the user's profile visibility is ``private``.

    ``members``-only profiles are included (the feed is unauthenticated; RSS
    has no auth mechanism).  Operators who need stricter privacy should set
    the profile to ``private``.
    """
    user: User | None = db.session.scalar(select(User).where(User.username == username))
    if user is None:
        return None

    # Privacy gate: private profile → don't expose an author feed.
    privacy = user.privacy_settings
    if privacy is not None and privacy.profile_visibility == ProfileVisibility.private:
        return None

    site_name: str = current_app.config.get("SITE_NAME", "OpenBlog")
    author_display = user.display_name or user.username
    author_link = absolute_url(url_for("users.profile", username=username))
    feed_url = absolute_url(url_for("feed.author_feed", username=username))

    posts = list(
        db.session.scalars(
            select(Post)
            .where(
                Post.workspace_id.is_(None),
                Post.status == PostStatus.published,
                Post.published_at.is_not(None),
                Post.author_id == user.id,
            )
            .options(joinedload(Post.author), joinedload(Post.tags))
            .order_by(Post.published_at.desc(), Post.id.desc())
            .limit(limit)
        ).unique()
    )

    post_ids = [p.id for p in posts]
    last_modified = _compute_last_modified(post_ids)

    channel = _channel(
        title=f"{author_display} — {site_name}",
        description=f"Posts by {author_display} on {site_name}",
        link=author_link,
        feed_url=feed_url,
    )
    return channel, [_post_to_item(p) for p in posts], last_modified


# ── JSON Feed v1.1 helpers ────────────────────────────────────────────────────


def _iso_utc(dt: datetime | None) -> str:
    """Return *dt* as an ISO 8601 UTC string ending in ``Z``.

    Falls back to the epoch sentinel when *dt* is ``None``.
    """
    if dt is None:
        return _EPOCH.strftime("%Y-%m-%dT%H:%M:%SZ")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_to_json_item(post: Post) -> dict[str, Any]:
    """Convert a *Post* to a JSON Feed item dict (v1.1 spec).

    - ``id`` and ``url`` are canonical absolute URLs.
    - ``authors`` contains only display names — no email addresses.
    - ``content_text`` uses the SEO description or a plain-text excerpt.
    Routes prompts to /prompts/<slug> and articles to /posts/<slug>.
    """
    if post.kind == "prompt":
        post_url = absolute_url(url_for("prompts.public_prompt_detail", slug=post.slug))
    else:
        post_url = absolute_url(url_for("posts.post_detail", slug=post.slug))
    item: dict[str, Any] = {
        "id": post_url,
        "url": post_url,
        "title": post.title,
        "content_text": post.seo_description or _to_excerpt(post.markdown_body),
        "date_published": _iso_utc(post.published_at or post.updated_at),
        "authors": [{"name": post.author.display_name or post.author.username}],
        "tags": [t.name for t in post.tags],
    }
    if post.updated_at:
        item["date_modified"] = _iso_utc(post.updated_at)
    return item


def _json_feed_meta(
    title: str,
    description: str,
    home_page_url: str,
    feed_url: str,
) -> dict[str, Any]:
    """Return the top-level JSON Feed v1.1 metadata dict (without ``items``)."""
    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": title,
        "home_page_url": home_page_url,
        "feed_url": feed_url,
        "description": description,
        "language": "en",
    }


# ── JSON Feed public API ──────────────────────────────────────────────────────


def get_global_json_feed(
    limit: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime]:
    """Return ``(feed_meta, items, last_modified)`` for the site-wide JSON feed."""
    site_name: str = current_app.config.get("SITE_NAME", "OpenBlog")
    home_url = absolute_url("/")
    feed_url = absolute_url(url_for("json_feed.global_json_feed"))

    posts = list(db.session.scalars(_published_query(limit)).unique())
    post_ids = [p.id for p in posts]
    last_modified = _compute_last_modified(post_ids)

    meta = _json_feed_meta(
        title=site_name,
        description=f"Latest posts from {site_name}",
        home_page_url=home_url,
        feed_url=feed_url,
    )
    return meta, [_post_to_json_item(p) for p in posts], last_modified


def get_tag_json_feed(
    slug: str,
    limit: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime] | None:
    """Return ``(feed_meta, items, last_modified)`` for a tag JSON feed.

    Returns ``None`` if the tag does not exist (caller should 404).
    """
    tag: Tag | None = db.session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is None:
        return None

    site_name: str = current_app.config.get("SITE_NAME", "OpenBlog")
    tag_link = absolute_url(url_for("posts.list_posts", tag=slug))
    feed_url = absolute_url(url_for("json_feed.tag_json_feed", slug=slug))

    posts = list(
        db.session.scalars(
            select(Post)
            .where(
                Post.workspace_id.is_(None),
                Post.status == PostStatus.published,
                Post.published_at.is_not(None),
                Post.tags.any(Tag.slug == slug),
            )
            .options(joinedload(Post.author), joinedload(Post.tags))
            .order_by(Post.published_at.desc(), Post.id.desc())
            .limit(limit)
        ).unique()
    )

    post_ids = [p.id for p in posts]
    last_modified = _compute_last_modified(post_ids)

    meta = _json_feed_meta(
        title=f"#{tag.name} — {site_name}",
        description=tag.description or f"Posts tagged #{tag.name} on {site_name}",
        home_page_url=tag_link,
        feed_url=feed_url,
    )
    return meta, [_post_to_json_item(p) for p in posts], last_modified


def get_author_json_feed(
    username: str,
    limit: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime] | None:
    """Return ``(feed_meta, items, last_modified)`` for an author JSON feed.

    Returns ``None`` if:
    - the user does not exist, or
    - the user's profile visibility is ``private``.
    """
    user: User | None = db.session.scalar(select(User).where(User.username == username))
    if user is None:
        return None

    privacy = user.privacy_settings
    if privacy is not None and privacy.profile_visibility == ProfileVisibility.private:
        return None

    site_name: str = current_app.config.get("SITE_NAME", "OpenBlog")
    author_display = user.display_name or user.username
    author_link = absolute_url(url_for("users.profile", username=username))
    feed_url = absolute_url(url_for("json_feed.author_json_feed", username=username))

    posts = list(
        db.session.scalars(
            select(Post)
            .where(
                Post.workspace_id.is_(None),
                Post.status == PostStatus.published,
                Post.published_at.is_not(None),
                Post.author_id == user.id,
            )
            .options(joinedload(Post.author), joinedload(Post.tags))
            .order_by(Post.published_at.desc(), Post.id.desc())
            .limit(limit)
        ).unique()
    )

    post_ids = [p.id for p in posts]
    last_modified = _compute_last_modified(post_ids)

    meta = _json_feed_meta(
        title=f"{author_display} — {site_name}",
        description=f"Posts by {author_display} on {site_name}",
        home_page_url=author_link,
        feed_url=feed_url,
    )
    return meta, [_post_to_json_item(p) for p in posts], last_modified
