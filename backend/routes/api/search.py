"""JSON API — search endpoint.

Route
-----
GET /api/search?q=<query>&page=<n>&per_page=<n>

Returns a paginated list of published posts that match the full-text query.
The response includes a short excerpt for each result so clients can render
meaningful result cards without fetching the full post body.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.extensions import csrf
from backend.routes.api.posts import _post_dict
from backend.services.search_service import SearchService

api_search_bp = Blueprint("api_search", __name__, url_prefix="/api/search")
csrf.exempt(api_search_bp)


@api_search_bp.get("/")
def search():
    """Full-text search over published posts.

    Query parameters
    ----------------
    q        str   Search query (required; empty string returns empty results).
    page     int   1-based page number (default 1).
    per_page int   Results per page (default 20, max 100).
    """
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    results = SearchService.search(q, page, per_page)
    posts, total = results.posts, results.post_total

    return jsonify(
        {
            "query": q,
            "posts": [
                {
                    **_post_dict(p),
                    "excerpt": SearchService.excerpt(p.markdown_body or "", q),
                }
                for p in posts
            ],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if total else 0,
        }
    )
