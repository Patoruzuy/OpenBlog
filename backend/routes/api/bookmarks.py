"""JSON API — bookmark endpoints.

Routes
------
GET    /api/bookmarks/              list current user's bookmarks  [authenticated]
POST   /api/posts/<slug>/bookmark   add a bookmark                 [authenticated]
DELETE /api/posts/<slug>/bookmark   remove a bookmark              [authenticated]
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.extensions import csrf
from backend.services.bookmark_service import BookmarkError, BookmarkService
from backend.services.post_service import PostService
from backend.utils.auth import api_require_auth, get_current_user

api_bookmarks_bp = Blueprint("api_bookmarks", __name__, url_prefix="/api")
csrf.exempt(api_bookmarks_bp)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _post_stub(post) -> dict:
    """Minimal post representation for bookmark list items."""
    return {
        "id": post.id,
        "slug": post.slug,
        "title": post.title,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "reading_time_minutes": post.reading_time_minutes,
        "author": {
            "id": post.author_id,
            "username": post.author.username,
            "display_name": post.author.display_name,
        },
        "tags": [{"slug": t.slug, "name": t.name} for t in post.tags],
    }


# ── GET /api/bookmarks/ ───────────────────────────────────────────────────────


@api_bookmarks_bp.get("/bookmarks/")
@api_require_auth
def list_bookmarks():
    """Return the authenticated user's bookmarked posts, newest first."""
    user = get_current_user()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    posts, total = BookmarkService.list_for_user(user.id, page, per_page)
    return jsonify(
        {
            "posts": [_post_stub(p) for p in posts],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if total else 0,
        }
    )


# ── POST /api/posts/<slug>/bookmark ───────────────────────────────────────────


@api_bookmarks_bp.post("/posts/<slug>/bookmark")
@api_require_auth
def add_bookmark(slug: str):
    """Bookmark a published post."""
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    try:
        BookmarkService.add(user.id, post.id)
    except BookmarkError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify({"bookmarked": True})


# ── DELETE /api/posts/<slug>/bookmark ─────────────────────────────────────────


@api_bookmarks_bp.delete("/posts/<slug>/bookmark")
@api_require_auth
def remove_bookmark(slug: str):
    """Remove a bookmark from a post."""
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    try:
        BookmarkService.remove(user.id, post.id)
    except BookmarkError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify({"bookmarked": False})
