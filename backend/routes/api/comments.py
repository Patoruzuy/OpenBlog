"""JSON API — comment endpoints.

All endpoints are CSRF-exempt (Bearer tokens provide equivalent protection).

Routes
------
GET    /api/posts/<slug>/comments       list threaded comments (public for published posts)
POST   /api/posts/<slug>/comments       add a comment          [authenticated]
PUT    /api/comments/<id>               edit body              [authenticated — author only]
DELETE /api/comments/<id>               soft-delete            [authenticated — author/editor/admin]
POST   /api/comments/<id>/flag          flag for moderation    [authenticated]
POST   /api/comments/<id>/unflag        clear flag             [editor/admin]
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.extensions import csrf, db
from backend.models.comment import Comment
from backend.models.post import PostStatus
from backend.models.user import UserRole
from backend.services.comment_service import CommentError, CommentService
from backend.services.post_service import PostService
from backend.services.vote_service import VoteService
from backend.utils.auth import api_require_auth, get_current_user
from backend.schemas import CreateCommentSchema, UpdateCommentSchema, load_json

api_comments_bp = Blueprint("api_comments", __name__, url_prefix="/api")
csrf.exempt(api_comments_bp)

_EDITOR_ROLES = {UserRole.admin.value, UserRole.editor.value}


# ── Serialiser ────────────────────────────────────────────────────────────────


def _comment_dict(
    comment: Comment,
    *,
    include_replies: bool = True,
    include_flagged: bool = False,
    viewer_id: int | None = None,
) -> dict:
    """Serialise a Comment to a plain dict.

    Soft-deleted comments show "[deleted]" as body and omit author info to
    preserve thread structure while hiding the original content.
    Flagged replies are hidden unless *include_flagged* is ``True``.
    """
    d: dict = {
        "id": comment.id,
        "post_id": comment.post_id,
        "parent_id": comment.parent_id,
        "body": comment.body,
        "is_deleted": comment.is_deleted,
        "is_flagged": comment.is_flagged,
        "author": (
            None
            if comment.is_deleted
            else {
                "id": comment.author_id,
                "username": comment.author.username,
                "display_name": comment.author.display_name,
            }
        ),
        "vote_count": VoteService.vote_count("comment", comment.id),
        "created_at": comment.created_at.isoformat(),
        "updated_at": comment.updated_at.isoformat(),
    }
    if viewer_id is not None:
        d["has_voted"] = VoteService.has_voted(viewer_id, "comment", comment.id)
    if include_replies:
        replies = sorted(comment.replies, key=lambda r: r.created_at)
        if not include_flagged:
            replies = [r for r in replies if not r.is_flagged]
        d["replies"] = [
            _comment_dict(
                r,
                include_replies=False,
                include_flagged=include_flagged,
                viewer_id=viewer_id,
            )
            for r in replies
        ]
    return d


# ── GET /api/posts/<slug>/comments ────────────────────────────────────────────


@api_comments_bp.get("/posts/<slug>/comments")
def list_comments(slug: str):
    """Return the threaded comment tree for a published post.

    Editors and admins see flagged comments; everyone else sees only
    non-flagged comments.
    """
    post = PostService.get_by_slug(slug)
    if post is None or post.status != PostStatus.published:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    include_flagged = user is not None and user.role.value in _EDITOR_ROLES
    viewer_id = user.id if user is not None else None

    comments = CommentService.list_for_post(post.id, include_flagged=include_flagged)
    return jsonify(
        {
            "comments": [
                _comment_dict(c, include_flagged=include_flagged, viewer_id=viewer_id)
                for c in comments
            ],
            "total": len(comments),
        }
    )


# ── POST /api/posts/<slug>/comments ───────────────────────────────────────────


@api_comments_bp.post("/posts/<slug>/comments")
@api_require_auth
def create_comment(slug: str):
    """Create a comment (or reply) on a published post."""
    post = PostService.get_by_slug(slug)
    if post is None or post.status != PostStatus.published:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    data, err = load_json(CreateCommentSchema())
    if err:
        return err
    try:
        comment = CommentService.create(
            post.id,
            user.id,
            data["body"],
            parent_id=data["parent_id"],
        )
    except CommentError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(_comment_dict(comment)), 201


# ── PUT /api/comments/<id> ────────────────────────────────────────────────────


@api_comments_bp.put("/comments/<int:comment_id>")
@api_require_auth
def update_comment(comment_id: int):
    """Update the body of a comment (author only)."""
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return jsonify({"error": "Comment not found."}), 404

    user = get_current_user()
    data, err = load_json(UpdateCommentSchema())
    if err:
        return err
    try:
        comment = CommentService.update(
            comment, data["body"], editor_id=user.id
        )
    except CommentError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(_comment_dict(comment))


# ── DELETE /api/comments/<id> ─────────────────────────────────────────────────


@api_comments_bp.delete("/comments/<int:comment_id>")
@api_require_auth
def delete_comment(comment_id: int):
    """Soft-delete a comment (author, editor, or admin)."""
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return jsonify({"error": "Comment not found."}), 404

    user = get_current_user()
    try:
        CommentService.delete(comment, user_id=user.id, user_role=user.role.value)
    except CommentError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify({"deleted": True, "id": comment_id})


# ── POST /api/comments/<id>/flag ──────────────────────────────────────────────


@api_comments_bp.post("/comments/<int:comment_id>/flag")
@api_require_auth
def flag_comment(comment_id: int):
    """Flag a comment for moderation review (any authenticated user)."""
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return jsonify({"error": "Comment not found."}), 404

    CommentService.flag(comment)
    return jsonify({"flagged": True, "id": comment_id})


# ── POST /api/comments/<id>/unflag ────────────────────────────────────────────


@api_comments_bp.post("/comments/<int:comment_id>/unflag")
@api_require_auth
def unflag_comment(comment_id: int):
    """Clear the moderation flag (editor/admin only)."""
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return jsonify({"error": "Comment not found."}), 404

    user = get_current_user()
    try:
        CommentService.unflag(comment, user_role=user.role.value)
    except CommentError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify({"flagged": False, "id": comment_id})
