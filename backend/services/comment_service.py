"""Comment CRUD service.

All public methods are static and expect an active Flask application context
(i.e. an active ``db.session``).

Raises
------
CommentError  for all domain-rule violations (400 by default, 403/404 as needed).
"""

from __future__ import annotations

from sqlalchemy import select

from backend.extensions import db
from backend.models.comment import Comment
from backend.models.user import UserRole
from backend.utils import metrics


class CommentError(Exception):
    """Domain error raised by CommentService.  Carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_EDITOR_ROLES = {UserRole.admin.value, UserRole.editor.value}
_TOMBSTONE = "[deleted]"


class CommentService:
    # ── Create ────────────────────────────────────────────────────────────────

    @staticmethod
    def create(
        post_id: int,
        author_id: int,
        body: str,
        *,
        parent_id: int | None = None,
    ) -> Comment:
        """Create and persist a new comment.

        Only one level of nesting is allowed: a reply must itself be a
        top-level comment (i.e. ``parent.parent_id`` must be ``None``).

        Raises
        ------
        CommentError(400)  for empty body or depth > 1.
        CommentError(404)  if *parent_id* doesn't exist on the same post.
        """
        body = body.strip()
        if not body:
            raise CommentError("Comment body cannot be empty.")

        if parent_id is not None:
            parent = db.session.get(Comment, parent_id)
            if parent is None or parent.post_id != post_id:
                raise CommentError("Parent comment not found on this post.", 404)
            if parent.parent_id is not None:
                raise CommentError("Replies can only be one level deep.", 400)

        comment = Comment(
            post_id=post_id,
            author_id=author_id,
            body=body,
            parent_id=parent_id,
        )
        db.session.add(comment)
        db.session.commit()
        metrics.comments_created.inc()

        # Dispatch thread notification asynchronously.  Any error here must
        # not surface to the caller — a failed notification must never prevent
        # a comment from being saved.
        try:
            from backend.tasks.notifications import (
                notify_thread_comment_created,  # noqa: PLC0415
            )

            notify_thread_comment_created.delay(
                {
                    "post_id": post_id,
                    "comment_id": comment.id,
                    "author_id": author_id,
                    "parent_id": parent_id,
                    "body": body,
                }
            )
        except Exception:  # pragma: no cover
            from flask import current_app  # noqa: PLC0415

            current_app.logger.warning(
                "Failed to enqueue thread notification for comment %s", comment.id
            )

        return comment

    # ── Update ────────────────────────────────────────────────────────────────

    @staticmethod
    def update(comment: Comment, body: str, *, editor_id: int) -> Comment:
        """Update the body of a comment (author only).

        Raises
        ------
        CommentError(400)  if the comment is soft-deleted or body is empty.
        CommentError(403)  if *editor_id* is not the original author.
        """
        if comment.is_deleted:
            raise CommentError("Cannot edit a deleted comment.", 400)
        if comment.author_id != editor_id:
            raise CommentError("Only the author may edit this comment.", 403)
        body = body.strip()
        if not body:
            raise CommentError("Comment body cannot be empty.")
        comment.body = body
        db.session.commit()
        return comment

    # ── Delete (soft) ─────────────────────────────────────────────────────────

    @staticmethod
    def delete(comment: Comment, *, user_id: int, user_role: str) -> Comment:
        """Soft-delete a comment: replace body with a tombstone string.

        Authors, editors, and admins may delete.

        Raises
        ------
        CommentError(403)  if the user is neither the author nor a moderator.
        """
        if comment.author_id != user_id and user_role not in _EDITOR_ROLES:
            raise CommentError("Not authorised to delete this comment.", 403)
        comment.is_deleted = True
        comment.body = _TOMBSTONE
        db.session.commit()
        return comment

    # ── Flag / unflag ─────────────────────────────────────────────────────────

    @staticmethod
    def flag(comment: Comment) -> Comment:
        """Mark a comment as flagged for moderation review (idempotent)."""
        comment.is_flagged = True
        db.session.commit()
        return comment

    @staticmethod
    def unflag(comment: Comment, *, user_role: str) -> Comment:
        """Clear the moderation flag.  Only editors and admins may unflag.

        Raises
        ------
        CommentError(403)  if the caller lacks editor/admin role.
        """
        if user_role not in _EDITOR_ROLES:
            raise CommentError("Only editors and admins may unflag comments.", 403)
        comment.is_flagged = False
        db.session.commit()
        return comment

    # ── List ──────────────────────────────────────────────────────────────────

    @staticmethod
    def list_for_post(
        post_id: int,
        *,
        include_flagged: bool = False,
    ) -> list[Comment]:
        """Return top-level comments for *post_id*, ordered oldest-first.

        Each ``Comment.replies`` collection is populated lazily when accessed
        (SQLAlchemy ``lazy="select"``).

        Parameters
        ----------
        include_flagged:
            When ``False`` (default) flagged top-level comments are omitted.
            Pass ``True`` for moderator views.
        """
        stmt = (
            select(Comment)
            .where(Comment.post_id == post_id, Comment.parent_id.is_(None))
            .order_by(Comment.created_at.asc())
        )
        if not include_flagged:
            stmt = stmt.where(Comment.is_flagged.is_(False))
        return list(db.session.scalars(stmt))
