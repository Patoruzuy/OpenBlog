"""Recently-improved service — posts with accepted community revisions.

Surfaces a discovery feed of posts that received at least one accepted revision
within a configurable time window.  This reinforces OpenBlog's versioned-writing
identity by showing readers which articles are actively being improved.

Public API
----------
``RecentlyImprovedService.get_recently_improved_posts(*, days, limit)``
    Homepage feed — returns up to *limit* entries as a plain list.

``RecentlyImprovedService.list_improvements(*, days, page, per_page)``
    Paginated full listing used by ``GET /improvements``.  Returns a
    ``Page``-like dict with keys ``items``, ``page``, ``pages``, ``total``,
    ``per_page``.

Query strategy — no N+1
-----------------------
Three queries total regardless of *limit* / *per_page*:

1. A single GROUP BY aggregate on the ``revisions`` table **joined to
   ``posts``** (so the published filter is applied in SQL, not in Python) that
   computes ``accepted_count`` and ``last_accepted_at`` for each eligible post
   and returns only the requested page slice via LIMIT/OFFSET.  A separate
   COUNT query is executed first to compute pagination totals.

2. A single JOIN query (Revision ⋈ User) for the same post IDs that fetches
   the identity snapshot of the *latest* accepted revision per post.  Rows are
   ordered (post_id, reviewed_at DESC, id DESC) so the first occurrence per
   post_id in Python is always the winner; tie-break by revision ID ensures
   determinism when two revisions share the same ``reviewed_at``.

3. A single ``SELECT … WHERE id IN (…)`` on the ``posts`` table with
   ``joinedload`` for ``author`` and ``tags``, so templates can render those
   relationships without triggering additional queries.

The three result sets are merged in Python — O(per_page) cost, not O(posts).

Identity safety
---------------
``last_accepted_by_display`` is populated only when the revision's
``public_identity_mode`` is explicitly ``"public"`` or ``"pseudonymous"``.
Any other value (``"anonymous"`` or ``None`` / unknown) yields ``None`` so
no contributor identity is ever leaked.

Display name resolution (priority order, mode permitting):
  1. ``Revision.public_display_name_snapshot`` — captured at submission time.
  2. ``User.display_name`` — live profile field (already loaded via JOIN).
  3. ``User.username`` — final fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User


def _resolve_display(
    mode: str | None,
    snapshot: str | None,
    user_display_name: str | None,
    user_username: str | None,
) -> str | None:
    """Return a safe public display name or ``None`` if identity is private."""
    if mode not in ("public", "pseudonymous"):
        # Covers: "anonymous", None, or any unexpected future value.
        return None
    return snapshot or user_display_name or user_username or None


class RecentlyImprovedService:
    """Static-method service for the recently-improved homepage feed."""

    @staticmethod
    def get_recently_improved_posts(
        *,
        days: int = 30,
        limit: int = 6,
    ) -> list[dict]:
        """Return posts that received accepted revisions within the last *days* days.

        Parameters
        ----------
        days:
            How far back to look for accepted revisions (default: 30).
        limit:
            Maximum number of posts to return (default: 6).

        Returns
        -------
        list[dict]
            Each dict contains:

            ``post``
                :class:`~backend.models.post.Post` ORM object with ``author``
                and ``tags`` pre-loaded (no lazy hits in templates).
            ``accepted_count_in_window``
                Number of accepted revisions for this post in the window.
            ``last_accepted_at``
                :class:`~datetime.datetime` of the most recent accepted
                revision (``Revision.reviewed_at`` when status == accepted).
            ``last_accepted_by_display``
                Display name of the last contributor (respects anonymity
                settings) or ``None`` when unavailable or anonymous.
        """
        cutoff: datetime = datetime.now(UTC) - timedelta(days=days)

        # ── Query 1: aggregate accepted revisions in the window ──────────────
        # Filters:
        #   - status == accepted
        #   - reviewed_at (= accepted_at) >= cutoff
        # Groups by post_id, computes count + max(reviewed_at).
        # Orders by most-recently-improved descending.
        # Only takes *_agg_limit* rows — the visibility filter (published) is
        # applied in Query 3; we over-select to account for non-published posts.
        _agg_limit = max(limit * 3, 20)

        agg_rows = db.session.execute(
            select(
                Revision.post_id,
                func.count(Revision.id).label("accepted_count"),
                func.max(Revision.reviewed_at).label("last_accepted_at"),
            )
            .where(
                Revision.status == RevisionStatus.accepted,
                Revision.reviewed_at >= cutoff,
            )
            .group_by(Revision.post_id)
            .order_by(func.max(Revision.reviewed_at).desc())
            .limit(_agg_limit)
        ).all()

        if not agg_rows:
            return []

        # Build an ordered lookup: post_id → (count, last_at)
        agg_by_post_id: dict[int, tuple[int, datetime]] = {
            row.post_id: (int(row.accepted_count), row.last_accepted_at)
            for row in agg_rows
        }
        ordered_post_ids: list[int] = [row.post_id for row in agg_rows]

        # ── Query 2: identity of the latest accepted revision per post ───────
        # JOIN Revision ⋈ User in a single SQL statement.
        # Ordered (post_id, reviewed_at DESC, id DESC) so the first Python row
        # per post_id is always the winner; ID tie-break ensures determinism.
        id_rows = db.session.execute(
            select(
                Revision.post_id,
                Revision.public_identity_mode,
                Revision.public_display_name_snapshot,
                User.display_name.label("user_display_name"),
                User.username.label("user_username"),
            )
            .join(User, User.id == Revision.author_id)
            .where(
                Revision.post_id.in_(ordered_post_ids),
                Revision.status == RevisionStatus.accepted,
                Revision.reviewed_at >= cutoff,
            )
            .order_by(
                Revision.post_id,
                Revision.reviewed_at.desc(),
                Revision.id.desc(),
            )
        ).all()

        # First occurrence per post_id = latest accepted revision.
        identity_by_post_id: dict[int, str | None] = {}
        for row in id_rows:
            if row.post_id in identity_by_post_id:
                continue
            identity_by_post_id[row.post_id] = _resolve_display(
                row.public_identity_mode,
                row.public_display_name_snapshot,
                row.user_display_name,
                row.user_username,
            )

        # ── Query 3: load Post ORM objects with author + tags pre-fetched ────
        # Filters only published posts; silently drops drafts/archived.
        posts_by_id: dict[int, Post] = {
            post.id: post
            for post in db.session.execute(
                select(Post)
                .where(
                    Post.id.in_(ordered_post_ids),
                    Post.workspace_id.is_(None),
                    Post.status == PostStatus.published,
                )
                .options(
                    joinedload(Post.author),
                    joinedload(Post.tags),
                )
            )
            .unique()
            .scalars()
        }

        # ── Assemble results in order (most-recently-improved first) ─────────
        result: list[dict] = []
        for post_id in ordered_post_ids:
            post = posts_by_id.get(post_id)
            if post is None:
                # Post is not published — skip
                continue
            accepted_count, last_accepted_at = agg_by_post_id[post_id]
            result.append(
                {
                    "post": post,
                    "accepted_count_in_window": accepted_count,
                    "last_accepted_at": last_accepted_at,
                    "last_accepted_by_display": identity_by_post_id.get(post_id),
                }
            )
            if len(result) >= limit:
                break

        return result

    # ── Paginated listing ─────────────────────────────────────────────────────

    @staticmethod
    def list_improvements(
        *,
        days: int | None = 30,
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Return a paginated listing of posts improved via accepted revisions.

        Parameters
        ----------
        days:
            How far back to look.  Pass ``None`` (or the sentinel value
            ``"all"``) to include all time; any positive integer limits to the
            last *days* days.
        page:
            1-based page number.
        per_page:
            Rows per page (default: 20).

        Returns
        -------
        dict
            ``items``   – list of entry dicts (same shape as
                          :meth:`get_recently_improved_posts`).
            ``page``    – current page number.
            ``pages``   – total number of pages.
            ``total``   – total matching distinct post count.
            ``per_page``– rows per page.
        """
        # Build the optional cutoff predicate.
        cutoff: datetime | None = None
        if days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=int(days))

        # Shared WHERE clause expressions (reused across count + data queries).
        # INV-001: only public published posts.
        base_filters = [
            Revision.status == RevisionStatus.accepted,
            Post.workspace_id.is_(None),
            Post.status == PostStatus.published,
        ]
        if cutoff is not None:
            base_filters.append(Revision.reviewed_at >= cutoff)

        # ── Count query: total distinct matching post_ids ────────────────────
        count_q = (
            select(func.count(func.distinct(Revision.post_id)))
            .join(Post, Post.id == Revision.post_id)
            .where(*base_filters)
        )
        total: int = db.session.scalar(count_q) or 0

        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        offset = (page - 1) * per_page

        if total == 0:
            return {
                "items": [],
                "page": page,
                "pages": pages,
                "total": total,
                "per_page": per_page,
            }

        # ── Query 1: aggregate — count + max per post, paginated ─────────────
        agg_q = (
            select(
                Revision.post_id,
                func.count(Revision.id).label("accepted_count"),
                func.max(Revision.reviewed_at).label("last_accepted_at"),
            )
            .join(Post, Post.id == Revision.post_id)
            .where(*base_filters)
            .group_by(Revision.post_id)
            .order_by(
                func.max(Revision.reviewed_at).desc(),
                Revision.post_id.desc(),
            )
            .limit(per_page)
            .offset(offset)
        )
        agg_rows = db.session.execute(agg_q).all()

        if not agg_rows:
            return {
                "items": [],
                "page": page,
                "pages": pages,
                "total": total,
                "per_page": per_page,
            }

        agg_by_post_id: dict[int, tuple[int, datetime]] = {
            row.post_id: (int(row.accepted_count), row.last_accepted_at)
            for row in agg_rows
        }
        ordered_post_ids: list[int] = [row.post_id for row in agg_rows]

        # ── Query 2: identity of the latest accepted revision per post ───────
        id_filters = [
            Revision.post_id.in_(ordered_post_ids),
            Revision.status == RevisionStatus.accepted,
        ]
        if cutoff is not None:
            id_filters.append(Revision.reviewed_at >= cutoff)

        id_rows = db.session.execute(
            select(
                Revision.post_id,
                Revision.public_identity_mode,
                Revision.public_display_name_snapshot,
                User.display_name.label("user_display_name"),
                User.username.label("user_username"),
            )
            .join(User, User.id == Revision.author_id)
            .where(*id_filters)
            .order_by(
                Revision.post_id,
                Revision.reviewed_at.desc(),
                Revision.id.desc(),
            )
        ).all()

        identity_by_post_id: dict[int, str | None] = {}
        for row in id_rows:
            if row.post_id in identity_by_post_id:
                continue
            identity_by_post_id[row.post_id] = _resolve_display(
                row.public_identity_mode,
                row.public_display_name_snapshot,
                row.user_display_name,
                row.user_username,
            )

        # ── Query 3: bulk hydrate Posts with author + tags ───────────────────
        posts_by_id: dict[int, Post] = {
            post.id: post
            for post in db.session.execute(
                select(Post)
                .where(Post.id.in_(ordered_post_ids))
                .options(
                    joinedload(Post.author),
                    joinedload(Post.tags),
                )
            )
            .unique()
            .scalars()
        }

        # ── Assemble in page order ────────────────────────────────────────────
        items: list[dict] = []
        for post_id in ordered_post_ids:
            post = posts_by_id.get(post_id)
            if post is None:
                continue
            accepted_count, last_accepted_at = agg_by_post_id[post_id]
            items.append(
                {
                    "post": post,
                    "accepted_count_in_window": accepted_count,
                    "last_accepted_at": last_accepted_at,
                    "last_accepted_by_display": identity_by_post_id.get(post_id),
                }
            )

        return {
            "items": items,
            "page": page,
            "pages": pages,
            "total": total,
            "per_page": per_page,
        }
