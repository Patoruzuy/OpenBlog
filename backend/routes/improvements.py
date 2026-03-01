"""SSR — All Improvements listing page.

Routes
------
GET /improvements?days=7|30|90|all&page=N
    Paginated list of posts improved through accepted community revisions,
    ordered by most-recently-improved descending.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from backend.services.recently_improved_service import RecentlyImprovedService

improvements_bp = Blueprint("improvements", __name__, url_prefix="/improvements")

_VALID_DAYS = frozenset({"7", "30", "90", "all"})
_DEFAULT_DAYS_STR = "30"
_PER_PAGE = 20


@improvements_bp.get("/")
def improvements_index():
    """List all posts improved via accepted revisions, paginated."""
    # ── days filter ──────────────────────────────────────────────────────────
    days_str = request.args.get("days", _DEFAULT_DAYS_STR).strip().lower()
    if days_str not in _VALID_DAYS:
        days_str = _DEFAULT_DAYS_STR

    days: int | None = None if days_str == "all" else int(days_str)

    # ── page ─────────────────────────────────────────────────────────────────
    page = max(1, request.args.get("page", 1, type=int))

    result = RecentlyImprovedService.list_improvements(
        days=days,
        page=page,
        per_page=_PER_PAGE,
    )

    return render_template(
        "improvements/index.html",
        entries=result["items"],
        page=result["page"],
        pages=result["pages"],
        total=result["total"],
        per_page=result["per_page"],
        days_str=days_str,  # "7" | "30" | "90" | "all"
    )
