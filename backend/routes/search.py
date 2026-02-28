"""SSR search route — renders the /search results page."""

from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from backend.services.search_service import SearchResults, SearchService
from backend.utils.auth import get_current_user

ssr_search_bp = Blueprint("search", __name__, url_prefix="/search")

_VALID_TABS = {"posts", "topics", "people"}

# Max recent searches stored per user in Redis
_RECENT_LIMIT = 5
# Redis key TTL: 30 days
_RECENT_TTL = 30 * 24 * 3600


def _push_recent_search(user_id: int, query: str) -> None:
    """Store *query* in the user's recent-search Redis list (dedup, trim to 5)."""
    redis = current_app.extensions.get("redis")
    if redis is None or not query:
        return
    key = f"search:recent:{user_id}"
    try:
        redis.lrem(key, 0, query)       # remove existing duplicate position
        redis.lpush(key, query)         # push to front
        redis.ltrim(key, 0, _RECENT_LIMIT - 1)
        redis.expire(key, _RECENT_TTL)
    except Exception:
        pass


def _get_recent_searches(user_id: int) -> list[str]:
    """Return the user's last N search queries from Redis."""
    redis = current_app.extensions.get("redis")
    if redis is None:
        return []
    try:
        return redis.lrange(f"search:recent:{user_id}", 0, _RECENT_LIMIT - 1) or []
    except Exception:
        return []


@ssr_search_bp.get("/")
def search_results():
    """Render a paginated search results page.

    Query parameters
    ----------------
    q        str  Search query.
    tab      str  Active tab: "posts" (default), "topics", or "people".
    page     int  1-based page number (default 1).
    per_page int  Results per page (default 20, max 50).
    """
    q = (request.args.get("q") or "").strip()
    tab = request.args.get("tab", "posts")
    if tab not in _VALID_TABS:
        tab = "posts"
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(50, max(1, request.args.get("per_page", 20, type=int)))

    viewer = get_current_user()

    # Store recent search for authenticated users
    if q and viewer is not None:
        _push_recent_search(viewer.id, q)

    recent_searches: list[str] = (
        _get_recent_searches(viewer.id) if viewer is not None else []
    )

    results: SearchResults
    if q:
        results = SearchService.search(q, page, per_page)
    else:
        results = SearchResults(
            posts=[], tags=[], users=[],
            post_total=0, tag_total=0, user_total=0,
        )

    # Per-tab page counts
    post_pages = (results.post_total + per_page - 1) // per_page if results.post_total else 0
    tag_pages = (results.tag_total + per_page - 1) // per_page if results.tag_total else 0
    people_pages = (results.user_total + per_page - 1) // per_page if results.user_total else 0

    # Batch-fetch published post counts for visible users (avoids N+1)
    user_post_counts: dict[int, int] = {}
    if results.users:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from backend.extensions import db
        from backend.models.post import Post, PostStatus

        uid_list = [u.id for u in results.users]
        rows = db.session.execute(
            sa_select(Post.author_id, sa_func.count(Post.id))
            .where(
                Post.author_id.in_(uid_list),
                Post.status == PostStatus.published,
            )
            .group_by(Post.author_id)
        ).all()
        user_post_counts = {author_id: cnt for author_id, cnt in rows}

    return render_template(
        "search/results.html",
        query=q,
        tab=tab,
        posts=results.posts,
        tags=results.tags,
        users=results.users,
        post_total=results.post_total,
        tag_total=results.tag_total,
        user_total=results.user_total,
        page=page,
        per_page=per_page,
        post_pages=post_pages,
        tag_pages=tag_pages,
        people_pages=people_pages,
        # Legacy alias kept for any external caller
        pages=post_pages,
        user_post_counts=user_post_counts,
        recent_searches=recent_searches,
        excerpt=SearchService.excerpt,
        highlight=SearchService.highlight_terms,
    )


@ssr_search_bp.get("/suggest")
def suggest():
    """Return JSON search suggestions for the live nav dropdown.

    Query parameters
    ----------------
    q    str  Partial query string (min 2 chars, or empty for recent searches).

    Response
    --------
    {
        "posts":  [{"title":..., "slug":..., "excerpt":...}, ...],
        "tags":   [{"name":..., "slug":...}, ...],
        "users":  [{"username":..., "display_name":..., "avatar_url":...}, ...],
        "recent": ["query1", ...]  (authenticated users only)
    }
    """
    q = (request.args.get("q") or "").strip()
    viewer = get_current_user()

    # When query is too short, return only recent searches (if authenticated)
    if len(q) < 2:
        recent: list[str] = (
            _get_recent_searches(viewer.id) if viewer is not None else []
        )
        return jsonify({"posts": [], "tags": [], "users": [], "recent": recent})

    # Try Redis cache first (30-second TTL) — cache is NOT user-specific.
    cache_key = f"suggest:{q[:64].lower()}"
    redis = current_app.extensions.get("redis")
    cached_data: dict | None = None
    if redis is not None:
        try:
            raw = redis.get(cache_key)
            if raw:
                cached_data = json.loads(raw)
        except Exception:
            pass

    if cached_data is None:
        cached_data = SearchService.suggest(q)
        if redis is not None:
            try:
                redis.set(cache_key, json.dumps(cached_data), ex=30)
            except Exception:
                pass

    # Always add per-user recent searches on top of cacheable results
    recent_list: list[str] = (
        _get_recent_searches(viewer.id) if viewer is not None else []
    )
    return jsonify({**cached_data, "recent": recent_list})
