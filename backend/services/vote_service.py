"""Vote service — upvote/unvote posts and comments.

Rules
-----
- A user may vote on a post **or** a comment, not on their own content.
- Duplicate votes raise a 409 (unique constraint → IntegrityError).
- Upvoting a post credits the post author +1 reputation; unvoting reverses it.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.comment import Comment
from backend.models.post import Post
from backend.models.user import User
from backend.models.vote import Vote


class VoteError(Exception):
    """Domain error raised by VoteService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class VoteService:
    """Static-method service for upvote / unvote operations."""

    _VALID_TYPES: frozenset[str] = frozenset({"post", "comment"})

    # ── Core operations ───────────────────────────────────────────────────────

    @staticmethod
    def upvote(user_id: int, target_type: str, target_id: int) -> Vote:
        """Record an upvote.  Returns the new Vote.

        Raises
        ------
        VoteError 400  bad target_type or self-vote attempt
        VoteError 404  target not found
        VoteError 409  already voted
        """
        if target_type not in VoteService._VALID_TYPES:
            raise VoteError(f"Invalid target type: {target_type!r}.", 400)

        # Resolve target — validate existence and ownership
        if target_type == "post":
            target = db.session.get(Post, target_id)
            if target is None:
                raise VoteError("Post not found.", 404)
            author_id = target.author_id
        else:
            target = db.session.get(Comment, target_id)
            if target is None or target.is_deleted:
                raise VoteError("Comment not found.", 404)
            author_id = target.author_id

        if author_id == user_id:
            raise VoteError("You cannot vote on your own content.", 400)

        vote = Vote(user_id=user_id, target_type=target_type, target_id=target_id)
        db.session.add(vote)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            raise VoteError("Already voted.", 409)

        # Reputation fan-out: +1 to post author only (comment votes don't affect rep).
        # Use a SQL-level atomic increment to avoid a read-modify-write race condition
        # when two upvotes arrive concurrently.
        if target_type == "post":
            db.session.execute(
                update(User)
                .where(User.id == author_id)
                .values(reputation_score=User.reputation_score + 1)
            )

        db.session.commit()
        return vote

    @staticmethod
    def unvote(user_id: int, target_type: str, target_id: int) -> None:
        """Remove an existing upvote.

        Raises
        ------
        VoteError 404  no vote to remove
        """
        vote = db.session.scalar(
            select(Vote).where(
                Vote.user_id == user_id,
                Vote.target_type == target_type,
                Vote.target_id == target_id,
            )
        )
        if vote is None:
            raise VoteError("Vote not found.", 404)

        # Undo reputation
        if target_type == "post":
            post = db.session.get(Post, target_id)
            if post is not None:
                author = db.session.get(User, post.author_id)
                if author is not None:
                    author.reputation_score = max(0, (author.reputation_score or 0) - 1)

        db.session.delete(vote)
        db.session.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    @staticmethod
    def has_voted(user_id: int, target_type: str, target_id: int) -> bool:
        """Return True if *user_id* has upvoted the given target."""
        return (
            db.session.scalar(
                select(func.count(Vote.id)).where(
                    Vote.user_id == user_id,
                    Vote.target_type == target_type,
                    Vote.target_id == target_id,
                )
            )
            or 0
        ) > 0

    @staticmethod
    def vote_count(target_type: str, target_id: int) -> int:
        """Return the total upvote count for the given target."""
        return (
            db.session.scalar(
                select(func.count(Vote.id)).where(
                    Vote.target_type == target_type,
                    Vote.target_id == target_id,
                )
            )
            or 0
        )

    @staticmethod
    def vote_counts(target_type: str, ids: list[int]) -> dict[int, int]:
        """Return a mapping {target_id: upvote_count} for each id in *ids*.

        Fires a single IN-query instead of one query per item.
        """
        if not ids:
            return {}
        rows = db.session.execute(
            select(Vote.target_id, func.count(Vote.id).label("cnt"))
            .where(Vote.target_type == target_type, Vote.target_id.in_(ids))
            .group_by(Vote.target_id)
        ).all()
        result = {row.target_id: row.cnt for row in rows}
        # Ensure every requested id is present (missing → 0)
        return {i: result.get(i, 0) for i in ids}

    @staticmethod
    def voted_set(user_id: int, target_type: str, ids: list[int]) -> set[int]:
        """Return the set of target_ids (from *ids*) that *user_id* has voted on.

        Fires a single IN-query instead of one query per item.
        """
        if not ids:
            return set()
        rows = db.session.scalars(
            select(Vote.target_id).where(
                Vote.user_id == user_id,
                Vote.target_type == target_type,
                Vote.target_id.in_(ids),
            )
        ).all()
        return set(rows)
