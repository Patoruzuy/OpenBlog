"""PinnedPostService — manage pinned posts on user profiles."""

from __future__ import annotations

from sqlalchemy import func, select

from backend.extensions import db
from backend.models.pinned_post import _MAX_PINNED, PinnedPost
from backend.models.post import Post


class PinnedPostError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PinnedPostService:
    @staticmethod
    def get_pinned(user_id: int) -> list[Post]:
        """Return pinned posts for *user_id*, ordered by position ascending."""
        rows = list(
            db.session.execute(
                select(PinnedPost)
                .where(PinnedPost.user_id == user_id)
                .order_by(PinnedPost.position.asc(), PinnedPost.created_at.asc())
            ).scalars()
        )
        # Eagerly load the Post for each
        post_ids = [r.post_id for r in rows]
        if not post_ids:
            return []
        posts_by_id = {
            p.id: p
            for p in db.session.scalars(select(Post).where(Post.id.in_(post_ids))).all()
        }
        return [posts_by_id[r.post_id] for r in rows if r.post_id in posts_by_id]

    @staticmethod
    def pin(user_id: int, post_id: int) -> PinnedPost:
        """Pin *post_id* for *user_id*.

        Raises
        ------
        PinnedPostError(409)  already pinned
        PinnedPostError(400)  pin limit (_MAX_PINNED) reached
        PinnedPostError(403)  post does not belong to user
        """
        post = db.session.get(Post, post_id)
        if post is None or post.author_id != user_id:
            raise PinnedPostError("You can only pin your own posts.", 403)

        existing = db.session.scalar(
            select(PinnedPost).where(
                PinnedPost.user_id == user_id,
                PinnedPost.post_id == post_id,
            )
        )
        if existing is not None:
            raise PinnedPostError("Post is already pinned.", 409)

        count = (
            db.session.scalar(
                select(func.count(PinnedPost.id)).where(PinnedPost.user_id == user_id)
            )
            or 0
        )
        if count >= _MAX_PINNED:
            raise PinnedPostError(
                f"You can have at most {_MAX_PINNED} pinned posts.", 400
            )

        pinned = PinnedPost(user_id=user_id, post_id=post_id, position=count + 1)
        db.session.add(pinned)
        db.session.commit()
        return pinned

    @staticmethod
    def unpin(user_id: int, post_id: int) -> None:
        """Unpin *post_id* for *user_id*.  No-op if not pinned."""
        existing = db.session.scalar(
            select(PinnedPost).where(
                PinnedPost.user_id == user_id,
                PinnedPost.post_id == post_id,
            )
        )
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()
