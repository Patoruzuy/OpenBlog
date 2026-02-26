"""JSON API — analytics endpoints.

Routes
------
GET  /api/posts/<slug>/analytics    per-post stats  [author, editor, admin]
GET  /api/analytics/top-posts       site-wide ranking  [editor, admin]
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.extensions import csrf
from backend.services.analytics_service import AnalyticsService
from backend.services.post_service import PostService
from backend.utils.auth import api_require_auth, api_require_role, get_current_user

api_analytics_bp = Blueprint("api_analytics", __name__, url_prefix="/api")
csrf.exempt(api_analytics_bp)


# ── Per-post stats ────────────────────────────────────────────────────────────


@api_analytics_bp.get("/posts/<slug>/analytics")
@api_require_auth
def post_analytics(slug: str):
    """Return aggregated analytics for a single post.

    Accessible by the post's author, and by any editor or admin.
    """
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    is_privileged = user.role.value in {"admin", "editor"}
    if post.author_id != user.id and not is_privileged:
        return jsonify({"error": "Insufficient permissions."}), 403

    stats = AnalyticsService.get_post_stats(post.id)
    return jsonify(stats)


# ── Site-wide top posts ───────────────────────────────────────────────────────


@api_analytics_bp.get("/analytics/top-posts")
@api_require_role("editor", "admin")
def top_posts():
    """Return the top posts by view count over the last N days (editor+ only).

    Query parameters
    ----------------
    limit  — number of posts to return (default 10, max 50)
    days   — look-back window in days (default 30, max 365)
    """
    limit = min(int(request.args.get("limit", 10)), 50)
    days = min(int(request.args.get("days", 30)), 365)

    results = AnalyticsService.get_top_posts(limit=limit, days=days)
    return jsonify({"items": results, "limit": limit, "days": days})
