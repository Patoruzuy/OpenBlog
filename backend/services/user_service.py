"""User profile and follow/unfollow service.

All public methods are static and expect an active Flask application context.

Raises
------
UserServiceError  for all domain-rule violations.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.follow import Follow
from backend.models.notification import Notification
from backend.models.post import Post, PostStatus
from backend.models.user import User
from backend.utils.validation import validate_url


class UserServiceError(Exception):
    """Domain error raised by UserService.  Carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UserService:
    # ── Profile ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_by_username(username: str) -> User | None:
        """Return the User with *username* (case-insensitive), or ``None``."""
        return db.session.scalar(
            select(User).where(func.lower(User.username) == username.lower())
        )

    @staticmethod
    def update_profile(
        user: User,
        *,
        display_name: str | None = None,
        bio: str | None = None,
        avatar_url: str | None = None,
        website_url: str | None = None,
        github_url: str | None = None,
        tech_stack: str | None = None,
        location: str | None = None,
    ) -> User:
        """Update mutable profile fields on *user*.

        Only fields explicitly passed as non-``None`` are changed, so callers
        can do partial updates with a single call.
        """
        if display_name is not None:
            user.display_name = display_name.strip()
        if bio is not None:
            user.bio = bio.strip()
        if avatar_url is not None:
            user.avatar_url = validate_url(avatar_url.strip(), field="avatar_url")
        if website_url is not None:
            user.website_url = validate_url(website_url.strip(), field="website_url")
        if github_url is not None:
            user.github_url = validate_url(github_url.strip(), field="github_url")
        if tech_stack is not None:
            user.tech_stack = tech_stack.strip()
        if location is not None:
            user.location = location.strip()
        db.session.commit()
        return user

    # ── Aggregates ────────────────────────────────────────────────────────────

    @staticmethod
    def published_post_count(user_id: int) -> int:
        """Return the number of published posts authored by *user_id*."""
        return (
            db.session.scalar(
                select(func.count(Post.id)).where(
                    Post.author_id == user_id,
                    Post.status == PostStatus.published,
                )
            )
            or 0
        )

    @staticmethod
    def follower_count(user_id: int) -> int:
        """Return the number of users following *user_id*."""
        return (
            db.session.scalar(
                select(func.count(Follow.id)).where(Follow.followed_id == user_id)
            )
            or 0
        )

    @staticmethod
    def following_count(user_id: int) -> int:
        """Return the number of users that *user_id* is following."""
        return (
            db.session.scalar(
                select(func.count(Follow.id)).where(Follow.follower_id == user_id)
            )
            or 0
        )

    @staticmethod
    def is_following(follower_id: int, followed_id: int) -> bool:
        """Return ``True`` if *follower_id* already follows *followed_id*."""
        return (
            db.session.scalar(
                select(func.count(Follow.id)).where(
                    Follow.follower_id == follower_id,
                    Follow.followed_id == followed_id,
                )
            )
            or 0
        ) > 0

    # ── Follow / unfollow ─────────────────────────────────────────────────────

    @staticmethod
    def follow(follower_id: int, followed_id: int) -> Follow:
        """Create a follow relationship and send a notification to the followed user.

        Raises
        ------
        UserServiceError(400)  if *follower_id* == *followed_id*.
        UserServiceError(409)  if the follow already exists.
        UserServiceError(404)  if *followed_id* does not exist.
        """
        if follower_id == followed_id:
            raise UserServiceError("You cannot follow yourself.", 400)

        followed = db.session.get(User, followed_id)
        if followed is None:
            raise UserServiceError("User not found.", 404)

        follower = db.session.get(User, follower_id)

        follow = Follow(follower_id=follower_id, followed_id=followed_id)
        db.session.add(follow)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            raise UserServiceError("Already following this user.", 409)

        # Fan-out: notify the followed user.
        notification = Notification(
            user_id=followed_id,
            notification_type="new_follower",
            title=f"{follower.username} started following you.",
            payload=json.dumps({"follower_username": follower.username}),
        )
        db.session.add(notification)
        db.session.commit()
        return follow

    @staticmethod
    def unfollow(follower_id: int, followed_id: int) -> None:
        """Remove a follow relationship.

        Raises
        ------
        UserServiceError(404)  if the follow does not exist.
        """
        follow = db.session.scalar(
            select(Follow).where(
                Follow.follower_id == follower_id,
                Follow.followed_id == followed_id,
            )
        )
        if follow is None:
            raise UserServiceError("You are not following this user.", 404)
        db.session.delete(follow)
        db.session.commit()

    # ── Follower / following lists ─────────────────────────────────────────────

    @staticmethod
    def get_followers(
        user_id: int, page: int = 1, per_page: int = 20
    ) -> tuple[list[User], int]:
        """Return paginated list of users who follow *user_id*."""
        base = (
            select(User)
            .join(Follow, Follow.follower_id == User.id)
            .where(Follow.followed_id == user_id)
            .order_by(Follow.created_at.desc())
        )
        total = db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        users = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return users, total

    @staticmethod
    def get_following(
        user_id: int, page: int = 1, per_page: int = 20
    ) -> tuple[list[User], int]:
        """Return paginated list of users that *user_id* is following."""
        base = (
            select(User)
            .join(Follow, Follow.followed_id == User.id)
            .where(Follow.follower_id == user_id)
            .order_by(Follow.created_at.desc())
        )
        total = db.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        users = list(
            db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
        )
        return users, total
