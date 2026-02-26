"""SSR — Bookmarks page.

Routes
------
GET /bookmarks/    current user's saved posts, paginated, newest first
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from backend.services.bookmark_service import BookmarkService
from backend.utils.auth import get_current_user, require_auth

ssr_bookmarks_bp = Blueprint("bookmarks", __name__, url_prefix="/bookmarks")

_PER_PAGE = 15


@ssr_bookmarks_bp.get("/")
@require_auth
def bookmark_list():
    """Render the current user's bookmarked posts."""
    user = get_current_user()  # guaranteed non-None by @require_auth
    page = max(1, request.args.get("page", 1, type=int))

    posts, total = BookmarkService.list_for_user(user.id, page=page, per_page=_PER_PAGE)
    pages = (total + _PER_PAGE - 1) // _PER_PAGE if total else 0

    return render_template(
        "bookmarks/index.html",
        posts=posts,
        page=page,
        pages=pages,
        total=total,
    )
