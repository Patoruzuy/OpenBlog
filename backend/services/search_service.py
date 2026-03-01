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
from backend.models.revision import Revision, RevisionStatus
from backend.models.tag import PostTag, Tag
from backend.models.user import User
from backend.models.user_post_read import UserPostRead
from backend.services.search_ranking import (
    _ANON,
    score_person,
    score_post,
    score_tag,
)
from backend.services.search_ranking import (
    title_score as _title_score,
)
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
        *,
        user_id: int | None = None,
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
                posts=[],
                tags=[],
                users=[],
                post_total=0,
                tag_total=0,
                user_total=0,
            )

        metrics.search_queries.inc()
        page = max(1, page)
        per_page = min(100, max(1, per_page))

        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            posts, post_total = SearchService._search_postgres(q, page, per_page)
            tags, tag_total = SearchService._search_tags_postgres(q)
            users, user_total = SearchService._search_users(q, page, per_page)
        else:
            posts, post_total = SearchService._search_sqlite(q, page, per_page)
            tags, tag_total = SearchService._search_tags_sqlite(q)
            users, user_total = SearchService._search_users(q, page, per_page)

        # Apply weighted ranking heuristic to each result group.
        posts = SearchService._rank_posts(posts, q, user_id=user_id)
        tags = SearchService._rank_tags(tags, q)
        users = SearchService._rank_users(users, q)

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
            user_stmt = SearchService._users_suggest_stmt(like_pat, limit)
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
            user_stmt = SearchService._users_suggest_stmt(like_pat, limit)

        tag_stmt = (
            select(Tag.name, Tag.slug)
            .where(or_(Tag.name.ilike(like_pat), Tag.slug.ilike(like_pat)))
            .limit(limit)
        )

        post_rows = db.session.execute(post_stmt).all()
        tag_rows = db.session.execute(tag_stmt).all()
        user_rows = db.session.execute(user_stmt).all()

        # Re-rank each suggest group by title / name relevance.
        post_rows = sorted(
            post_rows, key=lambda r: _title_score(q, r.title or ""), reverse=True
        )
        tag_rows = sorted(tag_rows, key=lambda r: score_tag(q, r), reverse=True)
        user_rows = sorted(user_rows, key=lambda r: score_person(q, r), reverse=True)

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

        total = (
            db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        posts = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return posts, total

    @staticmethod
    def _search_tags_sqlite(q: str) -> tuple[list[Tag], int]:
        like_pat = f"%{q}%"
        base = (
            select(Tag)
            .where(or_(Tag.name.like(like_pat), Tag.slug.like(like_pat)))
            .order_by(Tag.name)
        )
        tags = list(db.session.scalars(base.limit(20)))
        total = (
            db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
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

        total = (
            db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        posts = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return posts, total

    @staticmethod
    def _search_tags_postgres(q: str) -> tuple[list[Tag], int]:
        like_pat = f"%{q}%"
        base = (
            select(Tag)
            .where(or_(Tag.name.ilike(like_pat), Tag.slug.ilike(like_pat)))
            .order_by(Tag.name)
        )
        tags = list(db.session.scalars(base.limit(20)))
        total = (
            db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
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
                        UserPrivacySettings.profile_visibility
                        == ProfileVisibility.public.value,
                        UserPrivacySettings.searchable_profile == True,  # noqa: E712
                        UserPrivacySettings.default_identity_mode
                        != IdentityMode.anonymous.value,
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
    def _search_users(q: str, page: int, per_page: int) -> tuple[list[User], int]:
        """Search user profiles by *q*.

        ``_public_user_base`` uses ``.ilike()`` which is case-insensitive on
        both SQLite and PostgreSQL, so a single implementation covers both.
        """
        like_pat = f"%{q}%"
        base = SearchService._public_user_base(like_pat).order_by(User.username)
        total = (
            db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        users = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return users, total

    @staticmethod
    def _users_suggest_stmt(like_pat: str, limit: int):
        """Suggest query for user profiles.

        Uses ``.ilike()`` throughout — on SQLite this compiles to a plain
        ``LIKE`` (which is already case-insensitive for ASCII), so a single
        implementation covers both dialects without branching.
        """
        return (
            select(User.username, User.display_name, User.avatar_url)
            .outerjoin(UserPrivacySettings, UserPrivacySettings.user_id == User.id)
            .where(
                User.is_active == True,  # noqa: E712
                User.is_shadow_banned == False,  # noqa: E712
                or_(
                    UserPrivacySettings.id == None,  # noqa: E711
                    and_(
                        UserPrivacySettings.profile_visibility
                        == ProfileVisibility.public.value,
                        UserPrivacySettings.searchable_profile == True,  # noqa: E712
                        UserPrivacySettings.default_identity_mode
                        != IdentityMode.anonymous.value,
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

    # ── Ranking helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _bulk_read_map(user_id: int, post_ids: list[int]) -> dict[int, int]:
        """Return ``{post_id: last_read_version}`` for *user_id* × *post_ids*.

        Uses a single IN-query — no N+1 per post.
        """
        if not post_ids:
            return {}
        rows = db.session.execute(
            select(UserPostRead.post_id, UserPostRead.last_read_version).where(
                UserPostRead.user_id == user_id,
                UserPostRead.post_id.in_(post_ids),
            )
        ).all()
        return {post_id: version for post_id, version in rows}

    @staticmethod
    def _bulk_revision_counts(post_ids: list[int]) -> dict[int, int]:
        """Return ``{post_id: accepted_count}`` for all *post_ids*.

        Uses a single grouped IN-query — no N+1 per post.
        """
        if not post_ids:
            return {}
        rows = db.session.execute(
            select(Revision.post_id, func.count(Revision.id))
            .where(
                Revision.post_id.in_(post_ids),
                Revision.status == RevisionStatus.accepted,
            )
            .group_by(Revision.post_id)
        ).all()
        return {post_id: count for post_id, count in rows}

    @staticmethod
    def _bulk_tag_slugs(post_ids: list[int]) -> dict[int, list[str]]:
        """Return ``{post_id: [slug, ...]}`` for all *post_ids*.

        Avoids lazy-loading ``Post.tags`` relationships one-by-one.
        """
        if not post_ids:
            return {}
        rows = db.session.execute(
            select(PostTag.c.post_id, Tag.slug)
            .join(Tag, Tag.id == PostTag.c.tag_id)
            .where(PostTag.c.post_id.in_(post_ids))
        ).all()
        result: dict[int, list[str]] = {}
        for post_id, slug in rows:
            result.setdefault(post_id, []).append(slug)
        return result

    @staticmethod
    def _rank_posts(
        posts: list[Post],
        query: str,
        *,
        user_id: int | None = None,
    ) -> list[Post]:
        """Re-rank *posts* by the weighted scoring heuristic.

        Fetches read-history, accepted-revision counts and tag slugs in bulk
        (3 extra queries at most, regardless of result-set size) then sorts
        in Python.  Does NOT hit the database once per post.
        """
        if not posts:
            return posts

        post_ids = [p.id for p in posts]
        read_map = SearchService._bulk_read_map(user_id, post_ids) if user_id else {}
        rev_counts = SearchService._bulk_revision_counts(post_ids)
        tag_map = SearchService._bulk_tag_slugs(post_ids)

        def _sort_key(post: Post):
            # Determine personalisation read_version.
            if user_id is None:
                rv = _ANON  # anonymous — no boost
            else:
                rv = read_map.get(post.id)  # None → never read

            s = score_post(
                query,
                post,
                tag_slugs=tag_map.get(post.id, []),
                accepted_revision_count=rev_counts.get(post.id, 0),
                read_version=rv,
            )
            # Deterministic tiebreakers: newest updated first, then by id.
            upd_ts = post.updated_at.timestamp() if post.updated_at else 0.0
            pub_ts = post.published_at.timestamp() if post.published_at else 0.0
            return (-s, -upd_ts, -pub_ts, -post.id)

        return sorted(posts, key=_sort_key)

    @staticmethod
    def _rank_tags(tags: list[Tag], query: str) -> list[Tag]:
        """Re-rank *tags* by ``score_tag``."""
        if not tags:
            return tags
        return sorted(tags, key=lambda t: -score_tag(query, t))

    @staticmethod
    def _rank_users(users: list[User], query: str) -> list[User]:
        """Re-rank *users* by ``score_person``."""
        if not users:
            return users
        return sorted(users, key=lambda u: -score_person(query, u))
