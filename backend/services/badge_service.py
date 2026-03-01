"""Badge service — award and query gamification badges.

Badge definitions live in the ``badges`` table so new ones can be added
without a code deploy.  ``BadgeService.seed_defaults()`` populates the
well-known set of badge definitions that the rest of the codebase awards
programmatically (e.g. RevisionService calls it after accepting a revision).

Awarding is idempotent: calling ``award()`` twice for the same user + badge
key is a no-op (the ``UniqueConstraint`` on ``user_badges`` is used as the
guard).

Default badge keys
------------------
first_accepted_revision  — contributor's first revision was accepted
prolific_author          — author has published 5 or more posts
helpful_commenter        — user has posted 10 or more comments
popular_post             — one of the user's posts reached 50 upvotes
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.badge import Badge, UserBadge


class BadgeError(Exception):
    """Domain error raised by BadgeService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── Default badge catalogue ───────────────────────────────────────────────────

_DEFAULT_BADGES: list[dict] = [
    {
        "key": "first_accepted_revision",
        "name": "First Contribution",
        "description": "Your first proposed revision was accepted.",
        "icon_url": None,
    },
    {
        "key": "prolific_author",
        "name": "Prolific Author",
        "description": "Published 5 or more posts.",
        "icon_url": None,
    },
    {
        "key": "helpful_commenter",
        "name": "Helpful Commenter",
        "description": "Posted 10 or more comments.",
        "icon_url": None,
    },
    {
        "key": "popular_post",
        "name": "Popular Post",
        "description": "One of your posts reached 50 upvotes.",
        "icon_url": None,
    },
    {
        "key": "first_post",
        "name": "First Post",
        "description": "Published your very first post.",
        "icon_url": None,
    },
    {
        "key": "consistent_contributor",
        "name": "Consistent Contributor",
        "description": "Published 10 or more posts.",
        "icon_url": None,
    },
    {
        "key": "topic_contributor",
        "name": "Topic Contributor",
        "description": "Published posts across 3 or more distinct topics.",
        "icon_url": None,
    },
]


class BadgeService:
    """Static-method service for badge definition management and awarding."""

    # ── Seed ─────────────────────────────────────────────────────────────────

    @staticmethod
    def seed_defaults() -> list[Badge]:
        """Ensure the default badge definitions exist in the database.

        Safe to call multiple times — existing badges are skipped (upsert by
        key).  Returns the full list of default ``Badge`` objects.

        This is called automatically by ``award()`` when the badge definition
        is not yet present (lazy-seed pattern) so tests don't need an explicit
        setup step.
        """
        badges: list[Badge] = []
        for spec in _DEFAULT_BADGES:
            existing = db.session.scalar(select(Badge).where(Badge.key == spec["key"]))
            if existing is None:
                badge = Badge(
                    key=spec["key"],
                    name=spec["name"],
                    description=spec["description"],
                    icon_url=spec["icon_url"],
                )
                db.session.add(badge)
                db.session.flush()
                badges.append(badge)
            else:
                badges.append(existing)
        # db.session.commit()  # REMOVED: let caller commit
        return badges

    # ── Award ─────────────────────────────────────────────────────────────────

    @staticmethod
    def award(user_id: int, badge_key: str) -> UserBadge | None:
        """Award *badge_key* to *user_id*.

        Returns the new ``UserBadge`` row, or ``None`` if the user already
        holds the badge (idempotent — does **not** raise).

        Raises
        ------
        BadgeError 404  badge key does not exist (and cannot be auto-seeded)
        BadgeError 404  user does not exist
        """
        from backend.models.user import User

        user = db.session.get(User, user_id)
        if user is None:
            raise BadgeError("User not found.", 404)

        # Lazy-seed so tests don't need an explicit seed call.
        badge = db.session.scalar(select(Badge).where(Badge.key == badge_key))
        if badge is None:
            # Try seeding defaults; if the key is still missing it's unknown.
            BadgeService.seed_defaults()
            badge = db.session.scalar(select(Badge).where(Badge.key == badge_key))
        if badge is None:
            raise BadgeError(f"Unknown badge key: {badge_key!r}.", 404)

        # Create the savepoint BEFORE adding the object so that
        # begin_nested()'s auto-flush does not include the new row
        # (which would raise IntegrityError before sp is assigned).
        sp = db.session.begin_nested()
        user_badge = UserBadge(user_id=user_id, badge_id=badge.id)
        db.session.add(user_badge)
        try:
            db.session.flush()
            sp.commit()
        except IntegrityError:
            # Already awarded — roll back to savepoint so the outer
            # transaction remains intact, then return None.
            sp.rollback()
            return None

        return user_badge

    # ── Query helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def has_badge(user_id: int, badge_key: str) -> bool:
        """Return True if *user_id* holds *badge_key*."""
        badge = db.session.scalar(select(Badge).where(Badge.key == badge_key))
        if badge is None:
            return False
        return (
            db.session.scalar(
                select(func.count()).where(
                    UserBadge.user_id == user_id,
                    UserBadge.badge_id == badge.id,
                )
            )
            or 0
        ) > 0

    @staticmethod
    def list_for_user(user_id: int) -> list[UserBadge]:
        """Return all ``UserBadge`` rows for *user_id*, newest first."""
        return list(
            db.session.scalars(
                select(UserBadge)
                .where(UserBadge.user_id == user_id)
                .order_by(UserBadge.awarded_at.desc())
            )
        )

    @staticmethod
    def get_by_key(badge_key: str) -> Badge | None:
        """Return the ``Badge`` definition for *badge_key*, or None."""
        return db.session.scalar(select(Badge).where(Badge.key == badge_key))

    @staticmethod
    def list_all_definitions() -> list[Badge]:
        """Return all badge definitions, ordered by key."""
        return list(db.session.scalars(select(Badge).order_by(Badge.key)))
