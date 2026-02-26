"""Bookmark service — save posts for later reading.

Rules
-----
- Only published posts can be bookmarked.
- Duplicate bookmarks raise 409.
- list_for_user returns the Post objects (not Bookmark rows) ordered by
  bookmark creation time, newest first.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.bookmark import Bookmark
from backend.models.post import Post, PostStatus
from backend.utils import metrics


class BookmarkError(Exception):
    """Domain error raised by BookmarkService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BookmarkService:
    """Static-method service for bookmarking posts."""

    @staticmethod
    def add(user_id: int, post_id: int) -> Bookmark:
        """Bookmark *post_id* for *user_id*.

        Raises
        ------
        BookmarkError 404  post not found or not published
        BookmarkError 409  already bookmarked
        """
        post = db.session.get(Post, post_id)
        if post is None or post.status != PostStatus.published:
            raise BookmarkError("Post not found.", 404)

        bm = Bookmark(user_id=user_id, post_id=post_id)
        db.session.add(bm)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            raise BookmarkError("Already bookmarked.", 409)

        db.session.commit()
        metrics.bookmarks_created.inc()
        return bm

    @staticmethod
    def remove(user_id: int, post_id: int) -> None:
        """Remove a bookmark.

        Raises
        ------
        BookmarkError 404  bookmark not found
        """
        bm = db.session.scalar(
            select(Bookmark).where(
                Bookmark.user_id == user_id,
                Bookmark.post_id == post_id,
            )
        )
        if bm is None:
            raise BookmarkError("Bookmark not found.", 404)

        db.session.delete(bm)
        db.session.commit()

    @staticmethod
    def has_bookmarked(user_id: int, post_id: int) -> bool:
        """Return True if *user_id* has bookmarked *post_id*."""
        return (
            db.session.scalar(
                select(func.count(Bookmark.id)).where(
                    Bookmark.user_id == user_id,
                    Bookmark.post_id == post_id,
                )
            )
            or 0
        ) > 0

    @staticmethod
    def list_for_user(
        user_id: int, page: int = 1, per_page: int = 20
    ) -> tuple[list[Post], int]:
        """Return paginated posts bookmarked by *user_id*, newest bookmark first."""
        base = (
            select(Post)
            .join(Bookmark, Bookmark.post_id == Post.id)
            .where(Bookmark.user_id == user_id)
            .order_by(Bookmark.created_at.desc())
        )
        total = (
            db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        posts = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return posts, total
