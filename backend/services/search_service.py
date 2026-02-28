"""Full-text search service.

Two back-ends are supported, selected at runtime by inspecting the database
dialect:

SQLite (tests / development)
    Uses ``LIKE`` pattern matching on ``posts.title``, ``posts.markdown_body``
    and on the names/slugs of associated tags.  Case-insensitive because
    SQLite's LIKE is case-insensitive for ASCII characters by default.

PostgreSQL (production)
    Uses ``to_tsvector`` / ``websearch_to_tsquery`` on a concatenated
    ``title || body`` document, plus the same ILIKE tag fallback.  Results
    are ranked by ``ts_rank``.

Both paths accept ``page`` / ``per_page`` parameters and return a
``SearchResults`` named-tuple that carries separate post, tag and user result
sets.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from sqlalchemy import and_, func, or_, select, text

from backend.extensions import db
from backend.models.portal import IdentityMode, ProfileVisibility, UserPrivacySettings
from backend.models.post import Post, PostStatus
from backend.models.tag import PostTag, Tag
from backend.models.user import User
from backend.utils import metrics

# Maximum characters kept from a body excerpt for the snippet helper.
_SNIPPET_LENGTH = 200


class SearchResults(NamedTuple):
    """Aggregated results for a single search query."""

    posts: list[Post]
    tags: list[Tag]
    users: list[User]
    post_total: int
    tag_total: int
    user_total: int


class SearchService:
    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def search(
        query: str,
        page: int = 1,
        per_page: int = 20,
    ) -> SearchResults:
        """Search published posts, tags and public user profiles by *query*.

        Parameters
        ----------
        query:
            Raw search string from the user.  Empty / whitespace-only queries
            return an empty ``SearchResults`` immediately.
        page:
            1-based page number (applies to posts and users; tags always return
            up to 20).
        per_page:
            Maximum results per page for posts/users (clamped to 1-100).

        Returns
        -------
        SearchResults
            Named-tuple with ``posts``, ``tags``, ``users``, ``post_total``,
            ``tag_total``, ``user_total``.
        """
        q = query.strip()
        if not q:
            return SearchResults(
                posts=[], tags=[], users=[],
                post_total=0, tag_total=0, user_total=0,
            )

        metrics.search_queries.inc()
        page = max(1, page)
        per_page = min(100, max(1, per_page))

        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            posts, post_total = SearchService._search_postgres(q, page, per_page)
            tags, tag_total = SearchService._search_tags_postgres(q)
            users, user_total = SearchService._search_users_postgres(q, page, per_page)
        else:
            posts, post_total = SearchService._search_sqlite(q, page, per_page)
            tags, tag_total = SearchService._search_tags_sqlite(q)
            users, user_total = SearchService._search_users_sqlite(q, page, per_page)

        return SearchResults(
            posts=posts,
            tags=tags,
            users=users,
            post_total=post_total,
            tag_total=tag_total,
            user_total=user_total,
        )

    @staticmethod
    def suggest(query: str, limit: int = 5) -> dict:
        """Return lightweight suggestions for the live search dropdown.

        Returns a dict ``{"posts": [...], "tags": [...], "users": [...]}``
        suitable for JSON serialisation.  Each post entry has ``title``,
        ``slug``, ``excerpt``; each tag entry has ``name``, ``slug``; each
        user entry has ``username``, ``display_name``, ``avatar_url``.
        """
        q = query.strip()
        if not q or len(q) < 2:
            return {"posts": [], "tags": [], "users": []}

        dialect = db.engine.dialect.name
        like_pat = f"%{q}%"

        if dialect == "postgresql":
            tsvec = func.to_tsvector(
                text("'english'"),
                func.coalesce(Post.title, text("''"))
                + text("' '")
                + func.coalesce(Post.markdown_body, text("''")),
            )
            tsq = func.websearch_to_tsquery(text("'english'"), q)
            post_stmt = (
                select(Post.id, Post.title, Post.slug, Post.markdown_body)
                .where(
                    Post.status == PostStatus.published,
                    or_(tsvec.op("@@")(tsq), Post.title.ilike(like_pat)),
                )
                .order_by(func.ts_rank(tsvec, tsq).desc())
                .limit(limit)
            )
            user_stmt = SearchService._users_suggest_stmt_postgres(like_pat, limit)
        else:
            post_stmt = (
                select(Post.id, Post.title, Post.slug, Post.markdown_body)
                .where(
                    Post.status == PostStatus.published,
                    or_(Post.title.like(like_pat), Post.markdown_body.like(like_pat)),
                )
                .order_by(Post.published_at.desc())
                .limit(limit)
            )
            user_stmt = SearchService._users_suggest_stmt_sqlite(like_pat, limit)

        tag_stmt = (
            select(Tag.name, Tag.slug)
            .where(or_(Tag.name.ilike(like_pat), Tag.slug.ilike(like_pat)))
            .limit(limit)
        )

        post_rows = db.session.execute(post_stmt).all()
        tag_rows = db.session.execute(tag_stmt).all()
        user_rows = db.session.execute(user_stmt).all()

        posts_out = [
            {
                "title": row.title,
                "slug": row.slug,
                "excerpt": SearchService.excerpt(row.markdown_body or "", q, 80),
            }
            for row in post_rows
        ]
        tags_out = [{"name": row.name, "slug": row.slug} for row in tag_rows]
        users_out = [
            {
                "username": row.username,
                "display_name": row.display_name or row.username,
                "avatar_url": row.avatar_url,
            }
            for row in user_rows
        ]
        return {"posts": posts_out, "tags": tags_out, "users": users_out}

    @staticmethod
    def excerpt(body: str, query: str, length: int = _SNIPPET_LENGTH) -> str:
        """Return a short excerpt of *body* centred around the first hit of *query*.

        Falls back to the first *length* characters when the query term is not
        found.  Used by templates to render a useful result snippet.
        """
        lower_body = body.lower()
        lower_q = query.strip().lower()
        idx = lower_body.find(lower_q)
        if idx == -1:
            return body[:length].rstrip() + ("…" if len(body) > length else "")

        start = max(0, idx - length // 3)
        end = min(len(body), start + length)
        snippet = body[start:end].rstrip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(body) else ""
        return prefix + snippet + suffix

    @staticmethod
    def highlight_terms(text_: str, query: str) -> str:
        """Wrap each search term in *query* with ``<mark>`` tags inside *text_*.

        Safe for use in Jinja2 templates rendered with ``| safe`` — the caller
        is responsible for HTML-escaping *text_* before passing it in.
        """
        terms = [t for t in query.strip().split() if len(t) >= 2]
        if not terms:
            return text_
        pattern = re.compile(
            "(" + "|".join(re.escape(t) for t in terms) + ")",
            re.IGNORECASE,
        )
        return pattern.sub(r"<mark>\1</mark>", text_)

    # ── SQLite back-end ───────────────────────────────────────────────────────

    @staticmethod
    def _search_sqlite(q: str, page: int, per_page: int) -> tuple[list[Post], int]:
        like_pat = f"%{q}%"

        # Sub-query: IDs of posts whose tags match the query term.
        tag_post_ids = (
            select(PostTag.c.post_id)
            .join(Tag, Tag.id == PostTag.c.tag_id)
            .where(or_(Tag.name.like(like_pat), Tag.slug.like(like_pat)))
            .scalar_subquery()
        )

        base = (
            select(Post)
            .where(
                Post.status == PostStatus.published,
                or_(
                    Post.title.like(like_pat),
                    Post.markdown_body.like(like_pat),
                    Post.id.in_(tag_post_ids),
                ),
            )
            .order_by(Post.published_at.desc())
        )

        total = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        posts = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return posts, total

    @staticmethod
    def _search_tags_sqlite(q: str) -> tuple[list[Tag], int]:
        like_pat = f"%{q}%"
        base = select(Tag).where(
            or_(Tag.name.like(like_pat), Tag.slug.like(like_pat))
        ).order_by(Tag.name)
        tags = list(db.session.scalars(base.limit(20)))
        total = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        return tags, total

    # ── PostgreSQL back-end ───────────────────────────────────────────────────

    @staticmethod
    def _search_postgres(q: str, page: int, per_page: int) -> tuple[list[Post], int]:
        """Use ``tsvector`` / ``websearch_to_tsquery`` for ranked full-text search."""
        # Build a tsvector from title (weight A) + markdown_body (weight B).
        tsvec = func.to_tsvector(
            text("'english'"),
            func.coalesce(Post.title, text("''"))
            + text("' '")
            + func.coalesce(Post.markdown_body, text("''")),
        )
        tsq = func.websearch_to_tsquery(text("'english'"), q)

        like_pat = f"%{q}%"
        tag_post_ids = (
            select(PostTag.c.post_id)
            .join(Tag, Tag.id == PostTag.c.tag_id)
            .where(or_(Tag.name.ilike(like_pat), Tag.slug.ilike(like_pat)))
            .scalar_subquery()
        )

        base = (
            select(Post)
            .where(
                Post.status == PostStatus.published,
                or_(
                    tsvec.op("@@")(tsq),
                    Post.id.in_(tag_post_ids),
                ),
            )
            .order_by(func.ts_rank(tsvec, tsq).desc(), Post.published_at.desc())
        )

        total = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        posts = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return posts, total

    @staticmethod
    def _search_tags_postgres(q: str) -> tuple[list[Tag], int]:
        like_pat = f"%{q}%"
        base = select(Tag).where(
            or_(Tag.name.ilike(like_pat), Tag.slug.ilike(like_pat))
        ).order_by(Tag.name)
        tags = list(db.session.scalars(base.limit(20)))
        total = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        return tags, total

    # ── User search helpers ───────────────────────────────────────────────────

    @staticmethod
    def _public_user_base(like_pat: str):
        """Shared WHERE clause: public, searchable, non-anonymous user profiles."""
        return (
            select(User)
            .outerjoin(UserPrivacySettings, UserPrivacySettings.user_id == User.id)
            .where(
                User.is_active == True,  # noqa: E712
                User.is_shadow_banned == False,  # noqa: E712
                # Include users with NO privacy row (defaults to public/searchable)
                # or users who have explicitly enabled public visibility.
                or_(
                    UserPrivacySettings.id == None,  # noqa: E711 (IS NULL sentinel)
                    and_(
                        UserPrivacySettings.profile_visibility == ProfileVisibility.public.value,
                        UserPrivacySettings.searchable_profile == True,  # noqa: E712
                        UserPrivacySettings.default_identity_mode != IdentityMode.anonymous.value,
                    ),
                ),
                # Match username, display_name, or headline
                or_(
                    User.username.ilike(like_pat),
                    User.display_name.ilike(like_pat),
                    User.headline.ilike(like_pat),
                ),
            )
        )

    @staticmethod
    def _search_users_sqlite(q: str, page: int, per_page: int) -> tuple[list[User], int]:
        like_pat = f"%{q}%"
        base = SearchService._public_user_base(like_pat).order_by(User.username)
        total = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        users = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return users, total

    @staticmethod
    def _search_users_postgres(q: str, page: int, per_page: int) -> tuple[list[User], int]:
        like_pat = f"%{q}%"
        base = SearchService._public_user_base(like_pat).order_by(User.username)
        total = db.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        users = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return users, total

    @staticmethod
    def _users_suggest_stmt_sqlite(like_pat: str, limit: int):
        """Suggest query for user profiles (SQLite LIKE)."""
        return (
            select(User.username, User.display_name, User.avatar_url)
            .outerjoin(UserPrivacySettings, UserPrivacySettings.user_id == User.id)
            .where(
                User.is_active == True,  # noqa: E712
                User.is_shadow_banned == False,  # noqa: E712
                or_(
                    UserPrivacySettings.id == None,  # noqa: E711
                    and_(
                        UserPrivacySettings.profile_visibility == ProfileVisibility.public.value,
                        UserPrivacySettings.searchable_profile == True,  # noqa: E712
                        UserPrivacySettings.default_identity_mode != IdentityMode.anonymous.value,
                    ),
                ),
                or_(
                    User.username.like(like_pat),
                    User.display_name.like(like_pat),
                    User.headline.like(like_pat),
                ),
            )
            .order_by(User.username)
            .limit(limit)
        )

    @staticmethod
    def _users_suggest_stmt_postgres(like_pat: str, limit: int):
        """Suggest query for user profiles (Postgres ILIKE)."""
        return (
            select(User.username, User.display_name, User.avatar_url)
            .outerjoin(UserPrivacySettings, UserPrivacySettings.user_id == User.id)
            .where(
                User.is_active == True,  # noqa: E712
                User.is_shadow_banned == False,  # noqa: E712
                or_(
                    UserPrivacySettings.id == None,  # noqa: E711
                    and_(
                        UserPrivacySettings.profile_visibility == ProfileVisibility.public.value,
                        UserPrivacySettings.searchable_profile == True,  # noqa: E712
                        UserPrivacySettings.default_identity_mode != IdentityMode.anonymous.value,
                    ),
                ),
                or_(
                    User.username.ilike(like_pat),
                    User.display_name.ilike(like_pat),
                    User.headline.ilike(like_pat),
                ),
            )
            .order_by(User.username)
            .limit(limit)
        )
