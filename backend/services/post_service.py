"""Post service — all business logic for creating and managing blog posts.

Responsibilities
----------------
- Slug generation (unique, URL-safe, derived from title)
- Reading-time estimation
- Create / update / publish / schedule / archive / delete
- Version bumping on content changes
- HTML cache invalidation on content changes
- Paginated listing with optional tag filter
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import func, select

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.tag import Tag
from backend.models.user import User
from backend.utils import metrics
from backend.utils.markdown import invalidate_html_cache, reading_time_minutes
from backend.utils.validation import validate_url


class PostError(Exception):
    """Raised for domain-level errors in PostService.

    ``status_code`` maps directly to an HTTP response status.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Slug helpers ───────────────────────────────────────────────────────────────


# Slugs that must never be assigned to a post because they collide with
# static/wildcard routes.  Checked both here and in the SSR new-post form.
RESERVED_SLUGS: frozenset[str] = frozenset({"new", "edit", "draft", "preview"})


def _slugify(text: str) -> str:
    """Convert *text* to a lower-case, hyphen-separated, URL-safe string."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)   # strip punctuation (keep word chars, spaces, -)
    text = re.sub(r"[\s_]+", "-", text)    # spaces / underscores → hyphen
    text = re.sub(r"-{2,}", "-", text)     # collapse repeated hyphens
    return text.strip("-") or "untitled"


def _unique_slug(base: str) -> str:
    """Return *base* suffixed with -2, -3 … until it is unique in the DB.

    Uses a single prefix-query to fetch all existing slugs matching *base*
    rather than one round-trip per counter value.  Reserved slugs are treated
    as already-taken so they always get a numeric suffix.
    """
    existing = set(
        db.session.scalars(
            select(Post.slug).where(Post.slug.like(f"{base}%"))
        ).all()
    ) | RESERVED_SLUGS
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


# ── Tag helper ─────────────────────────────────────────────────────────────────


def _resolve_tags(tag_names: list[str]) -> list[Tag]:
    """Return Tag objects for each name in *tag_names*, creating missing ones.

    Uses a single ``WHERE slug IN (…)`` query to load all existing tags, then
    creates only the missing ones — avoiding N+1 round-trips.
    """
    # Build (name, slug) pairs, preserving order and skipping blank entries.
    pairs: list[tuple[str, str]] = []
    seen_slugs: set[str] = set()
    for raw in tag_names:
        name = raw.strip()
        if not name:
            continue
        slug = _slugify(name)
        if slug not in seen_slugs:
            pairs.append((name, slug))
            seen_slugs.add(slug)

    if not pairs:
        return []

    slugs = [slug for _, slug in pairs]

    # Batch fetch all existing tags in one query.
    with db.session.no_autoflush:
        existing: dict[str, Tag] = {
            t.slug: t
            for t in db.session.scalars(select(Tag).where(Tag.slug.in_(slugs))).all()
        }
        tags: list[Tag] = []
        for name, slug in pairs:
            tag = existing.get(slug)
            if tag is None:
                tag = Tag(name=name, slug=slug)
                db.session.add(tag)
            tags.append(tag)

    # One explicit flush to assign PKs to any newly added tags.
    db.session.flush()
    return tags


# ── Service ────────────────────────────────────────────────────────────────────


class PostService:
    # ── Create ────────────────────────────────────────────────────────────────

    @staticmethod
    def create(
        author_id: int,
        title: str,
        markdown_body: str = "",
        *,
        tags: list[str] | None = None,
        seo_title: str | None = None,
        seo_description: str | None = None,
        og_image_url: str | None = None,
    ) -> Post:
        """Create a draft post and return it.

        Raises
        ------
        PostError(400)  if *title* is empty.
        """
        title = title.strip()
        if not title:
            raise PostError("Title is required.", 400)

        # Compute slug first (no pending post in session yet, so no identity clash).
        slug = _unique_slug(_slugify(title))

        try:
            og_image_url = validate_url(og_image_url, field="og_image_url")
        except ValueError as exc:
            raise PostError(str(exc), 400) from exc

        post = Post(
            author_id=author_id,
            title=title,
            slug=slug,
            markdown_body=markdown_body,
            reading_time_minutes=reading_time_minutes(markdown_body),
            seo_title=seo_title,
            seo_description=seo_description,
            og_image_url=og_image_url,
        )
        db.session.add(post)
        # Flush to assign a PK before resolving tags.  This prevents the
        # back_populates cascade from inserting a second Post row (identity-map
        # collision) when tag.posts is lazily loaded during tag assignment.
        db.session.flush()

        if tags:
            resolved = _resolve_tags(tags)
            post.tags = resolved

        db.session.commit()
        metrics.posts_created.inc()
        return post

    # ── Update ────────────────────────────────────────────────────────────────

    @staticmethod
    def update(
        post: Post,
        *,
        title: str | None = None,
        markdown_body: str | None = None,
        tags: list[str] | None = None,
        seo_title: str | None = None,
        seo_description: str | None = None,
        og_image_url: str | None = None,
    ) -> Post:
        """Update *post* in-place and return it.

        The version counter is bumped only when ``markdown_body`` changes.
        The HTML cache is invalidated whenever a content update occurs.
        """
        content_changed = False

        if title is not None:
            title = title.strip()
            if not title:
                raise PostError("Title cannot be empty.", 400)
            post.title = title

        if markdown_body is not None and markdown_body != post.markdown_body:
            post.markdown_body = markdown_body
            post.reading_time_minutes = reading_time_minutes(markdown_body)
            post.version += 1
            content_changed = True

        if tags is not None:
            post.tags = _resolve_tags(tags)

        if seo_title is not None:
            post.seo_title = seo_title or None
        if seo_description is not None:
            post.seo_description = seo_description or None
        if og_image_url is not None:
            try:
                post.og_image_url = validate_url(og_image_url or None, field="og_image_url")
            except ValueError as exc:
                raise PostError(str(exc), 400) from exc

        db.session.commit()

        if content_changed:
            invalidate_html_cache(post.id)

        return post

    # ── Publish / Schedule / Archive ─────────────────────────────────────────

    @staticmethod
    def publish(post: Post, *, at: datetime | None = None) -> Post:
        """Publish *post* immediately, or schedule it for *at* (UTC datetime).

        Raises
        ------
        PostError(400) if *at* is in the past.
        """
        if at is not None:
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if at <= datetime.now(UTC):
                raise PostError("Scheduled publish time must be in the future.", 400)
            post.status = PostStatus.scheduled
            post.publish_at = at
        else:
            now = datetime.now(UTC)
            post.status = PostStatus.published
            post.published_at = now
            post.publish_at = None

        db.session.commit()
        if post.status == PostStatus.published:
            metrics.posts_published.inc()
            # ── Badge hooks on publish ────────────────────────────────────────
            try:
                from backend.services.badge_service import BadgeService  # noqa: PLC0415

                # first_post: author's first published post
                published_count = db.session.scalar(
                    select(func.count(Post.id))
                    .where(Post.author_id == post.author_id)
                    .where(Post.status == PostStatus.published)
                ) or 0
                if published_count == 1:
                    BadgeService.award(post.author_id, "first_post")

                # consistent_contributor: 10+ published posts
                if published_count >= 10:
                    BadgeService.award(post.author_id, "consistent_contributor")

                # topic_contributor: published posts in 3+ distinct tags
                from backend.models.tag import PostTag  # noqa: PLC0415

                distinct_tags = db.session.scalar(
                    select(func.count(func.distinct(PostTag.c.tag_id)))
                    .select_from(PostTag)
                    .join(Post, Post.id == PostTag.c.post_id)
                    .where(Post.author_id == post.author_id)
                    .where(Post.status == PostStatus.published)
                ) or 0
                if distinct_tags >= 3:
                    BadgeService.award(post.author_id, "topic_contributor")

                # prolific_author: 5+ published posts
                if published_count >= 5:
                    BadgeService.award(post.author_id, "prolific_author")
            except Exception:  # noqa: BLE001
                # Badge hooks must never break the publish flow.
                pass
        return post

    @staticmethod
    def archive(post: Post) -> Post:
        """Set *post* status to archived."""
        post.status = PostStatus.archived
        db.session.commit()
        return post

    # ── Delete ────────────────────────────────────────────────────────────────

    @staticmethod
    def delete(post: Post) -> None:
        """Hard-delete *post* from the database."""
        invalidate_html_cache(post.id)
        db.session.delete(post)
        db.session.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_by_slug(slug: str) -> Post | None:
        """Return the Post with *slug*, or None."""
        return db.session.scalar(select(Post).where(Post.slug == slug))

    @staticmethod
    def list_published(
        page: int = 1,
        per_page: int = 20,
        tag_slug: str | None = None,
    ) -> tuple[list[Post], int]:
        """Return (posts, total_count) for the requested page of published posts.

        When *tag_slug* is supplied only posts tagged with that tag are returned.
        Results are ordered newest-published-first.  Shadow-banned authors'
        posts are always excluded.
        """
        base = (
            select(Post)
            .join(User, User.id == Post.author_id)
            .where(
                Post.status == PostStatus.published,
                User.is_shadow_banned.is_(False),
            )
            .order_by(Post.published_at.desc())
        )
        if tag_slug:
            base = base.join(Post.tags).where(Tag.slug == tag_slug)

        total: int = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        posts = list(
            db.session.scalars(
                base.offset((page - 1) * per_page).limit(per_page)
            ).all()
        )
        return posts, total

    @staticmethod
    def list_all(
        page: int = 1,
        per_page: int = 20,
        tag_slug: str | None = None,
    ) -> tuple[list[Post], int]:
        """Like ``list_published`` but includes all statuses (for editors/admins)."""
        base = select(Post).order_by(Post.updated_at.desc())
        if tag_slug:
            base = base.join(Post.tags).where(Tag.slug == tag_slug)

        total: int = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        posts = list(
            db.session.scalars(
                base.offset((page - 1) * per_page).limit(per_page)
            ).all()
        )
        return posts, total

    @staticmethod
    def get_featured() -> Post | None:
        """Return the featured post, or the most-recently published one as fallback."""
        featured = db.session.scalar(
            select(Post)
            .join(User, User.id == Post.author_id)
            .where(
                Post.status == PostStatus.published,
                Post.is_featured.is_(True),
                User.is_shadow_banned.is_(False),
            )
            .order_by(Post.published_at.desc())
            .limit(1)
        )
        if featured:
            return featured
        # Fallback: latest published post.
        return db.session.scalar(
            select(Post)
            .join(User, User.id == Post.author_id)
            .where(
                Post.status == PostStatus.published,
                User.is_shadow_banned.is_(False),
            )
            .order_by(Post.published_at.desc())
            .limit(1)
        )

    @staticmethod
    def list_recently_updated(limit: int = 4) -> list[Post]:
        """Return up to *limit* published posts that have been revised (version > 1).

        Results are ordered by ``updated_at`` descending so the most-recently
        improved article appears first.
        """
        return list(
            db.session.scalars(
                select(Post)
                .join(User, User.id == Post.author_id)
                .where(
                    Post.status == PostStatus.published,
                    Post.version > 1,
                    User.is_shadow_banned.is_(False),
                )
                .order_by(Post.updated_at.desc())
                .limit(limit)
            ).all()
        )
