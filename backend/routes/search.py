"""SSR search route — renders the /search results page."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from backend.services.search_service import SearchService

ssr_search_bp = Blueprint("search", __name__, url_prefix="/search")


@ssr_search_bp.get("/")
def search_results():
    """Render a paginated search results page.

    Query parameters
    ----------------
    q        str  Search query.
    page     int  1-based page number (default 1).
    per_page int  Results per page (default 15, max 50).
    """
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(50, max(1, request.args.get("per_page", 15, type=int)))

    posts, total = SearchService.search(q, page, per_page) if q else ([], 0)
    pages = (total + per_page - 1) // per_page if total else 0

    return render_template(
        "search/results.html",
        query=q,
        posts=posts,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        excerpt=SearchService.excerpt,
    )
