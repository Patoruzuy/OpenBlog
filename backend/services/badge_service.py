"""Badge service — award and query gamification badges.

Badge definitions live in the ``badges`` table.  ``BadgeService.seed_defaults()``
populates the full catalog.  Awarding is idempotent: calling ``award()`` twice
for the same (user, badge, scope) is a no-op.

Scope rules
-----------
workspace_id IS NULL  -> public badge (visible on public profile)
workspace_id IS NOT NULL -> workspace-scoped badge (never visible publicly)

Uniqueness is enforced by two PARTIAL unique indexes on ``user_badges``:
  uq_user_badges_public: UNIQUE(user_id, badge_id) WHERE workspace_id IS NULL
  uq_user_badges_ws:     UNIQUE(user_id, badge_id, workspace_id)
                          WHERE workspace_id IS NOT NULL

Threshold sources in check_contribution_badges()
-------------------------------------------------
1. Accepted revisions      - user_analytics_service.build_user_contribution_summary
2. Ontology breadth        - direct count query over content_ontology / posts
3. Benchmarks run          - build_user_contribution_summary["benchmarks_run"]
4. A/B experiments created - build_user_contribution_summary["ab_experiments_created"]
5. A/B wins                - ReputationEvent where event_type='ab_win'
6. Upvotes received        - Vote table joined to user-authored posts (NOT rep totals)
7. Contribution streaks    - user_analytics_service.compute_contribution_streak
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.badge import Badge, UserBadge

log = logging.getLogger(__name__)


class BadgeError(Exception):
    """Domain error raised by BadgeService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# -- Default badge catalogue --------------------------------------------------

_DEFAULT_BADGES: list[dict] = [
    # Existing badges (kept verbatim, category + threshold added)
    {
        "key": "first_accepted_revision",
        "name": "First Contribution",
        "description": "Your first proposed revision was accepted.",
        "icon_url": "✅",
        "category": "contribution",
        "threshold": 1,
    },
    {
        "key": "prolific_author",
        "name": "Prolific Author",
        "description": "Published 5 or more posts.",
        "icon_url": "✍️",
        "category": "contribution",
        "threshold": 5,
    },
    {
        "key": "helpful_commenter",
        "name": "Helpful Commenter",
        "description": "Posted 10 or more comments.",
        "icon_url": "💬",
        "category": "contribution",
        "threshold": 10,
    },
    {
        "key": "popular_post",
        "name": "Popular Post",
        "description": "One of your posts reached 50 upvotes.",
        "icon_url": "🔥",
        "category": "impact",
        "threshold": 50,
    },
    {
        "key": "first_post",
        "name": "First Post",
        "description": "Published your very first post.",
        "icon_url": "🚀",
        "category": "contribution",
        "threshold": 1,
    },
    {
        "key": "consistent_contributor",
        "name": "Consistent Contributor",
        "description": "Published 10 or more posts.",
        "icon_url": "🏅",
        "category": "contribution",
        "threshold": 10,
    },
    {
        "key": "topic_contributor",
        "name": "Topic Contributor",
        "description": "Published posts across 3 or more distinct topics.",
        "icon_url": "🗂️",
        "category": "knowledge",
        "threshold": 3,
    },
    # New: Contribution — revisions
    {
        "key": "first_revision",
        "name": "First Revision",
        "description": "Your first revision was accepted.",
        "icon_url": "✏️",
        "category": "contribution",
        "threshold": 1,
    },
    {
        "key": "ten_revisions",
        "name": "Ten Revisions",
        "description": "10 accepted revisions — a true collaborator.",
        "icon_url": "📝",
        "category": "contribution",
        "threshold": 10,
    },
    {
        "key": "fifty_revisions",
        "name": "Fifty Revisions",
        "description": "50 accepted revisions — an editorial powerhouse.",
        "icon_url": "🖊️",
        "category": "contribution",
        "threshold": 50,
    },
    # New: Knowledge — ontology
    {
        "key": "ontology_explorer_5",
        "name": "Ontology Explorer",
        "description": "Contributed to 5 distinct knowledge topics.",
        "icon_url": "🔍",
        "category": "knowledge",
        "threshold": 5,
    },
    {
        "key": "ontology_explorer_10",
        "name": "Ontology Master",
        "description": "Contributed to 10 distinct knowledge topics.",
        "icon_url": "🧭",
        "category": "knowledge",
        "threshold": 10,
    },
    # New: Experimentation
    {
        "key": "first_benchmark",
        "name": "Benchmarker",
        "description": "Ran your first benchmark.",
        "icon_url": "⚗️",
        "category": "experimentation",
        "threshold": 1,
    },
    {
        "key": "first_ab_experiment",
        "name": "Experimenter",
        "description": "Created your first A/B experiment.",
        "icon_url": "🧪",
        "category": "experimentation",
        "threshold": 1,
    },
    {
        "key": "ab_winner",
        "name": "A/B Winner",
        "description": "Your variant won an A/B experiment.",
        "icon_url": "🏆",
        "category": "experimentation",
        "threshold": 1,
    },
    # New: Impact — upvotes
    {
        "key": "ten_upvotes",
        "name": "Rising Voice",
        "description": "Received 10 upvotes across your posts.",
        "icon_url": "👍",
        "category": "impact",
        "threshold": 10,
    },
    {
        "key": "hundred_upvotes",
        "name": "Community Favorite",
        "description": "Received 100 upvotes across your posts.",
        "icon_url": "💯",
        "category": "impact",
        "threshold": 100,
    },
    # New: Activity — streaks
    {
        "key": "streak_7",
        "name": "Week Streak",
        "description": "Contributed every day for 7 consecutive days.",
        "icon_url": "🔥",
        "category": "activity",
        "threshold": 7,
    },
    {
        "key": "streak_30",
        "name": "Month Streak",
        "description": "Contributed every day for 30 consecutive days.",
        "icon_url": "📅",
        "category": "activity",
        "threshold": 30,
    },
    {
        "key": "streak_100",
        "name": "Century Streak",
        "description": "Contributed every day for 100 consecutive days.",
        "icon_url": "💎",
        "category": "activity",
        "threshold": 100,
    },
]


# -- Helpers ------------------------------------------------------------------


def _count_ab_wins(user_id: int, workspace_id: int | None) -> int:
    from backend.models.reputation_event import ReputationEvent  # noqa: PLC0415

    stmt = select(func.count(ReputationEvent.id)).where(
        ReputationEvent.user_id == user_id,
        ReputationEvent.event_type == "ab_win",
    )
    if workspace_id is None:
        stmt = stmt.where(ReputationEvent.workspace_id.is_(None))
    else:
        stmt = stmt.where(
            or_(
                ReputationEvent.workspace_id.is_(None),
                ReputationEvent.workspace_id == workspace_id,
            )
        )
    return db.session.scalar(stmt) or 0


def _count_received_upvotes(user_id: int, workspace_id: int | None) -> int:
    from backend.models.post import Post, PostStatus  # noqa: PLC0415
    from backend.models.vote import Vote  # noqa: PLC0415

    stmt = (
        select(func.count(Vote.id))
        .join(Post, (Vote.target_type == "post") & (Vote.target_id == Post.id))
        .where(
            Post.author_id == user_id,
            Post.status == PostStatus.published.value,
        )
    )
    if workspace_id is None:
        stmt = stmt.where(Post.workspace_id.is_(None))
    else:
        stmt = stmt.where(
            or_(Post.workspace_id.is_(None), Post.workspace_id == workspace_id)
        )
    return db.session.scalar(stmt) or 0


def _count_ontology_nodes(user_id: int, public_only: bool) -> int:
    from backend.models.ontology import ContentOntology  # noqa: PLC0415
    from backend.models.post import Post, PostStatus  # noqa: PLC0415

    stmt = (
        select(func.count(func.distinct(ContentOntology.ontology_node_id)))
        .join(Post, ContentOntology.post_id == Post.id)
        .where(
            Post.author_id == user_id,
            Post.status == PostStatus.published.value,
        )
    )
    if public_only:
        stmt = stmt.where(
            Post.workspace_id.is_(None),
            ContentOntology.workspace_id.is_(None),
        )
    return db.session.scalar(stmt) or 0


# -- BadgeService -------------------------------------------------------------


class BadgeService:
    """Static-method service for badge definition management and awarding."""

    # -- Seed -----------------------------------------------------------------

    @staticmethod
    def seed_defaults() -> list[Badge]:
        """Ensure the default badge definitions exist in the database.

        Additive upsert — existing badges are updated in place; old badges
        already in the DB are never deleted.

        Safe to call multiple times (idempotent).
        """
        badges: list[Badge] = []
        for spec in _DEFAULT_BADGES:
            existing = db.session.scalar(select(Badge).where(Badge.key == spec["key"]))
            if existing is None:
                badge = Badge(
                    key=spec["key"],
                    name=spec["name"],
                    description=spec["description"],
                    icon_url=spec.get("icon_url"),
                    category=spec.get("category", "contribution"),
                    threshold=spec.get("threshold"),
                )
                db.session.add(badge)
                db.session.flush()
                badges.append(badge)
            else:
                existing.category = spec.get("category", existing.category)
                existing.threshold = spec.get("threshold", existing.threshold)
                if spec.get("icon_url") and not existing.icon_url:
                    existing.icon_url = spec["icon_url"]
                badges.append(existing)
        return badges

    # -- Award ----------------------------------------------------------------

    @staticmethod
    def award(
        user_id: int,
        badge_key: str,
        workspace_id: int | None = None,
    ) -> UserBadge | None:
        """Award *badge_key* to *user_id* in *workspace_id* scope.

        Returns the new ``UserBadge`` row, or ``None`` if the user already
        holds the badge in that scope (idempotent).

        Raises
        ------
        BadgeError 404  badge key does not exist
        BadgeError 404  user does not exist
        """
        from backend.models.user import User  # noqa: PLC0415

        user = db.session.get(User, user_id)
        if user is None:
            raise BadgeError("User not found.", 404)

        badge = db.session.scalar(select(Badge).where(Badge.key == badge_key))
        if badge is None:
            BadgeService.seed_defaults()
            badge = db.session.scalar(select(Badge).where(Badge.key == badge_key))
        if badge is None:
            raise BadgeError(f"Unknown badge key: {badge_key!r}.", 404)

        sp = db.session.begin_nested()
        user_badge = UserBadge(
            user_id=user_id,
            badge_id=badge.id,
            workspace_id=workspace_id,
        )
        db.session.add(user_badge)
        try:
            db.session.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            return None

        return user_badge

    # -- Bulk threshold checker -----------------------------------------------

    @staticmethod
    def check_contribution_badges(
        user_id: int,
        workspace_id: int | None = None,
    ) -> list[UserBadge]:
        """Evaluate all contribution thresholds and award applicable badges.

        Safe to call multiple times (award is idempotent per scope).
        Returns list of *newly* awarded UserBadge rows.
        """
        from backend.services import user_analytics_service as uas  # noqa: PLC0415

        awarded: list[UserBadge | None] = []
        public_only = workspace_id is None

        def _try(key: str) -> None:
            try:
                awarded.append(BadgeService.award(user_id, key, workspace_id))
            except Exception:  # noqa: BLE001
                log.warning("badge award failed: key=%s user=%s", key, user_id)

        # 1. Revision thresholds
        try:
            summary = uas.build_user_contribution_summary(
                user_id, public_only=public_only
            )
        except Exception:  # noqa: BLE001
            log.warning("badge check: analytics failed user=%s", user_id)
            summary = {
                "revisions_accepted": 0,
                "benchmarks_run": 0,
                "ab_experiments_created": 0,
            }

        ra = summary.get("revisions_accepted", 0)
        if ra >= 1:
            _try("first_revision")
        if ra >= 10:
            _try("ten_revisions")
        if ra >= 50:
            _try("fifty_revisions")

        # 2. Ontology breadth
        try:
            node_count = _count_ontology_nodes(user_id, public_only=public_only)
        except Exception:  # noqa: BLE001
            node_count = 0

        if node_count >= 5:
            _try("ontology_explorer_5")
        if node_count >= 10:
            _try("ontology_explorer_10")

        # 3. Benchmarks
        if summary.get("benchmarks_run", 0) >= 1:
            _try("first_benchmark")

        # 4. A/B experiments created
        if summary.get("ab_experiments_created", 0) >= 1:
            _try("first_ab_experiment")

        # 5. A/B wins
        try:
            if _count_ab_wins(user_id, workspace_id) >= 1:
                _try("ab_winner")
        except Exception:  # noqa: BLE001
            pass

        # 6. Upvotes
        try:
            upvotes = _count_received_upvotes(user_id, workspace_id)
            if upvotes >= 10:
                _try("ten_upvotes")
            if upvotes >= 100:
                _try("hundred_upvotes")
        except Exception:  # noqa: BLE001
            pass

        # 7. Streaks
        try:
            streak = uas.compute_contribution_streak(user_id, public_only=public_only)
            current = streak.get("current_streak", 0)
            if current >= 7:
                _try("streak_7")
            if current >= 30:
                _try("streak_30")
            if current >= 100:
                _try("streak_100")
        except Exception:  # noqa: BLE001
            pass

        return [ub for ub in awarded if ub is not None]

    # -- Query helpers --------------------------------------------------------

    @staticmethod
    def has_badge(
        user_id: int,
        badge_key: str,
        workspace_id: int | None = None,
    ) -> bool:
        """Return True if *user_id* holds *badge_key* in *workspace_id* scope."""
        badge = db.session.scalar(select(Badge).where(Badge.key == badge_key))
        if badge is None:
            return False
        stmt = select(func.count()).where(
            UserBadge.user_id == user_id,
            UserBadge.badge_id == badge.id,
        )
        if workspace_id is None:
            stmt = stmt.where(UserBadge.workspace_id.is_(None))
        else:
            stmt = stmt.where(UserBadge.workspace_id == workspace_id)
        return (db.session.scalar(stmt) or 0) > 0

    @staticmethod
    def list_for_user(
        user_id: int,
        workspace_id: int | None = None,
        public_only: bool = False,
    ) -> list[UserBadge]:
        """Return UserBadge rows for *user_id*, newest first.

        public_only=True  -> WHERE workspace_id IS NULL (safe for public profile)
        public_only=False -> all badges, optionally filtered to a workspace scope
        """
        stmt = (
            select(UserBadge)
            .where(UserBadge.user_id == user_id)
            .order_by(UserBadge.awarded_at.desc(), UserBadge.id.desc())
        )
        if public_only:
            stmt = stmt.where(UserBadge.workspace_id.is_(None))
        elif workspace_id is not None:
            stmt = stmt.where(
                or_(
                    UserBadge.workspace_id.is_(None),
                    UserBadge.workspace_id == workspace_id,
                )
            )
        return list(db.session.scalars(stmt))

    @staticmethod
    def get_by_key(badge_key: str) -> Badge | None:
        """Return the Badge definition for *badge_key*, or None."""
        return db.session.scalar(select(Badge).where(Badge.key == badge_key))

    @staticmethod
    def list_all_definitions() -> list[Badge]:
        """Return all badge definitions ordered by category then key."""
        return list(
            db.session.scalars(select(Badge).order_by(Badge.category, Badge.key))
        )

    @staticmethod
    def list_definitions_by_category() -> dict[str, list[Badge]]:
        """Return badge definitions grouped by category (each group key-sorted)."""
        badges = BadgeService.list_all_definitions()
        groups: dict[str, list[Badge]] = {}
        for badge in badges:
            groups.setdefault(badge.category, []).append(badge)
        return groups
