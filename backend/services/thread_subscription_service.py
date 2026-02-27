"""ThreadSubscriptionService — follow/unfollow comment threads on posts."""

from __future__ import annotations

from sqlalchemy import select

from backend.extensions import db
from backend.models.thread_subscription import ThreadSubscription


class ThreadSubscriptionError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ThreadSubscriptionService:

    @staticmethod
    def subscribe(user_id: int, post_id: int) -> ThreadSubscription:
        """Subscribe *user_id* to the comment thread for *post_id*.

        Idempotent — returns the existing subscription if already subscribed.
        """
        existing = db.session.scalar(
            select(ThreadSubscription).where(
                ThreadSubscription.user_id == user_id,
                ThreadSubscription.post_id == post_id,
            )
        )
        if existing is not None:
            return existing
        sub = ThreadSubscription(user_id=user_id, post_id=post_id)
        db.session.add(sub)
        db.session.commit()
        return sub

    @staticmethod
    def unsubscribe(user_id: int, post_id: int) -> None:
        """Unsubscribe *user_id* from the thread for *post_id*.

        No-op if not subscribed.
        """
        existing = db.session.scalar(
            select(ThreadSubscription).where(
                ThreadSubscription.user_id == user_id,
                ThreadSubscription.post_id == post_id,
            )
        )
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()

    @staticmethod
    def is_subscribed(user_id: int, post_id: int) -> bool:
        return (
            db.session.scalar(
                select(ThreadSubscription).where(
                    ThreadSubscription.user_id == user_id,
                    ThreadSubscription.post_id == post_id,
                )
            )
            is not None
        )

    @staticmethod
    def get_subscribers(post_id: int) -> list[int]:
        """Return a list of user IDs subscribed to *post_id*'s thread."""
        return list(
            db.session.scalars(
                select(ThreadSubscription.user_id).where(
                    ThreadSubscription.post_id == post_id
                )
            ).all()
        )
