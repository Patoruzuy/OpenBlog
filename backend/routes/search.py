"""SSR search route — renders the /search results page."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from backend.services.search_service import SearchResults, SearchService

ssr_search_bp = Blueprint("search", __name__, url_prefix="/search")

_VALID_TABS = {"posts", "topics"}


@ssr_search_bp.get("/")
def search_results():
    """Render a paginated search results page.

    Query parameters
    ----------------
    q        str  Search query.
    tab      str  Active tab: "posts" (default) or "topics".
    page     int  1-based page number (default 1).
    per_page int  Results per page (default 15, max 50).
    """
    q = (request.args.get("q") or "").strip()
    tab = request.args.get("tab", "posts")
    if tab not in _VALID_TABS:
        tab = "posts"
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(50, max(1, request.args.get("per_page", 15, type=int)))

    results: SearchResults
    if q:
        results = SearchService.search(q, page, per_page)
    else:
        results = SearchResults(posts=[], tags=[], post_total=0, tag_total=0)

    pages = (results.post_total + per_page - 1) // per_page if results.post_total else 0

    return render_template(
        "search/results.html",
        query=q,
        tab=tab,
        posts=results.posts,
        tags=results.tags,
        post_total=results.post_total,
        tag_total=results.tag_total,
        page=page,
        per_page=per_page,
        pages=pages,
        excerpt=SearchService.excerpt,
        highlight=SearchService.highlight_terms,
    )


@ssr_search_bp.get("/suggest")
def suggest():
    """Return JSON search suggestions for the live nav dropdown.

    Query parameters
    ----------------
    q    str  Partial query string (min 2 chars).

    Response
    --------
    {"posts": [{"title":..., "slug":..., "excerpt":...}, ...],
     "tags":  [{"name":..., "slug":...}, ...]}
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"posts": [], "tags": []})

    # Try Redis cache first (30-second TTL).
    from flask import current_app  # noqa: PLC0415

    cache_key = f"suggest:{q[:64].lower()}"
    redis = current_app.extensions.get("redis")
    if redis is not None:
        try:
            import json  # noqa: PLC0415

            cached = redis.get(cache_key)
            if cached:
                return jsonify(json.loads(cached))
        except Exception:
            pass

    data = SearchService.suggest(q)

    if redis is not None:
        try:
            import json  # noqa: PLC0415

            redis.set(cache_key, json.dumps(data), ex=30)
        except Exception:
            pass

    return jsonify(data)
