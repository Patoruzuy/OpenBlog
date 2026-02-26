"""JSON API — upvote / unvote endpoints for posts and comments.

Routes
------
POST   /api/posts/<slug>/vote      upvote a post        [authenticated]
DELETE /api/posts/<slug>/vote      remove post vote     [authenticated]
POST   /api/comments/<id>/vote     upvote a comment     [authenticated]
DELETE /api/comments/<id>/vote     remove comment vote  [authenticated]
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.extensions import csrf, db
from backend.models.comment import Comment
from backend.services.post_service import PostService
from backend.services.vote_service import VoteError, VoteService
from backend.utils.auth import api_require_auth, get_current_user

api_votes_bp = Blueprint("api_votes", __name__, url_prefix="/api")
csrf.exempt(api_votes_bp)


# ── Posts ─────────────────────────────────────────────────────────────────────


@api_votes_bp.post("/posts/<slug>/vote")
@api_require_auth
def vote_post(slug: str):
    """Upvote a published post."""
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    try:
        VoteService.upvote(user.id, "post", post.id)
    except VoteError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "voted": True,
            "vote_count": VoteService.vote_count("post", post.id),
        }
    )


@api_votes_bp.delete("/posts/<slug>/vote")
@api_require_auth
def unvote_post(slug: str):
    """Remove an upvote from a post."""
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    try:
        VoteService.unvote(user.id, "post", post.id)
    except VoteError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "voted": False,
            "vote_count": VoteService.vote_count("post", post.id),
        }
    )


# ── Comments ──────────────────────────────────────────────────────────────────


@api_votes_bp.post("/comments/<int:comment_id>/vote")
@api_require_auth
def vote_comment(comment_id: int):
    """Upvote a comment."""
    comment = db.session.get(Comment, comment_id)
    if comment is None or comment.is_deleted:
        return jsonify({"error": "Comment not found."}), 404

    user = get_current_user()
    try:
        VoteService.upvote(user.id, "comment", comment.id)
    except VoteError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "voted": True,
            "vote_count": VoteService.vote_count("comment", comment.id),
        }
    )


@api_votes_bp.delete("/comments/<int:comment_id>/vote")
@api_require_auth
def unvote_comment(comment_id: int):
    """Remove an upvote from a comment."""
    comment = db.session.get(Comment, comment_id)
    if comment is None or comment.is_deleted:
        return jsonify({"error": "Comment not found."}), 404

    user = get_current_user()
    try:
        VoteService.unvote(user.id, "comment", comment.id)
    except VoteError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "voted": False,
            "vote_count": VoteService.vote_count("comment", comment.id),
        }
    )
