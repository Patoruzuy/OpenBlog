"""Full-text search service.

Two back-ends are supported, selected at runtime by inspecting the database
dialect:

SQLite (tests / development)
    Uses ``LIKE`` pattern matching on ``posts.title``, ``posts.markdown_body``
    and on the names/slugs of associated tags.  Case-insensitive because
    SQLite's LIKE is case-insensitive for ASCII characters by default.

PostgreSQL (production)
    Uses ``to_tsvector`` / ``plainto_tsquery`` on a concatenated ``title ||
    body`` document, plus the same ILIKE tag fallback.  Results are ranked by
    ``ts_rank``.

Both paths return only ``PostStatus.published`` posts, accept ``page`` /
``per_page`` parameters, and return ``(posts, total)`` tuples.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select, text

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.tag import PostTag, Tag
from backend.utils import metrics

# Maximum characters kept from a body excerpt for the snippet helper.
_SNIPPET_LENGTH = 200


class SearchService:
    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def search(
        query: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Post], int]:
        """Search published posts by *query*.

        Parameters
        ----------
        query:
            Raw search string from the user.  Empty / whitespace-only queries
            return ``([], 0)`` immediately.
        page:
            1-based page number.
        per_page:
            Maximum results per page (clamped to 1-100).

        Returns
        -------
        (posts, total)
            *posts* is the current page slice; *total* is the total match count.
        """
        q = query.strip()
        if not q:
            return [], 0

        metrics.search_queries.inc()
        page = max(1, page)
        per_page = min(100, max(1, per_page))

        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            return SearchService._search_postgres(q, page, per_page)
        return SearchService._search_sqlite(q, page, per_page)

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

    # ── PostgreSQL back-end ───────────────────────────────────────────────────

    @staticmethod
    def _search_postgres(q: str, page: int, per_page: int) -> tuple[list[Post], int]:
        """Use ``tsvector`` / ``plainto_tsquery`` for ranked full-text search."""
        # Build a tsvector from title (weight A) + markdown_body (weight B).
        tsvec = func.to_tsvector(
            text("'english'"),
            func.coalesce(Post.title, text("''"))
            + text("' '")
            + func.coalesce(Post.markdown_body, text("''")),
        )
        tsq = func.plainto_tsquery(text("'english'"), q)

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
