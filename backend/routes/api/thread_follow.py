"""API routes for following / unfollowing comment threads."""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.models.post import Post
from backend.services.thread_subscription_service import ThreadSubscriptionService
from backend.utils.auth import api_require_auth, get_current_user

api_thread_follow_bp = Blueprint(
    "api_thread_follow", __name__, url_prefix="/api/posts"
)


def _get_post_or_404(slug: str) -> Post:
    post = Post.query.filter_by(slug=slug).first()
    if post is None:
        from flask import abort
        abort(404)
    return post


@api_thread_follow_bp.get("/<string:slug>/follow")
@api_require_auth
def get_follow(slug: str):
    """GET /api/posts/<slug>/follow — returns current subscription state."""
    post = _get_post_or_404(slug)
    following = ThreadSubscriptionService.is_subscribed(get_current_user().id, post.id)
    return jsonify({"following": following})


@api_thread_follow_bp.post("/<string:slug>/follow")
@api_require_auth
def follow(slug: str):
    """POST /api/posts/<slug>/follow — subscribe to thread."""
    post = _get_post_or_404(slug)
    ThreadSubscriptionService.subscribe(get_current_user().id, post.id)
    return jsonify({"following": True}), 201


@api_thread_follow_bp.delete("/<string:slug>/follow")
@api_require_auth
def unfollow(slug: str):
    """DELETE /api/posts/<slug>/follow — unsubscribe from thread."""
    post = _get_post_or_404(slug)
    ThreadSubscriptionService.unsubscribe(get_current_user().id, post.id)
    return jsonify({"following": False})
