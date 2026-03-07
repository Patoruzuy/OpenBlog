"""Tests for award idempotency — no duplicate rows per scope."""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import func, select

from backend.extensions import db
from backend.models.badge import UserBadge
from backend.services.badge_service import BadgeService

_ctr = itertools.count(100)


def _make_workspace(owner_id: int) -> object:
    from backend.models.workspace import Workspace  # noqa: PLC0415

    n = next(_ctr)
    ws = Workspace(name=f"Idem {n}", slug=f"idem-{n}", owner_id=owner_id)
    db.session.add(ws)
    db.session.flush()
    return ws


@pytest.fixture()
def user(make_user_token):
    u, _ = make_user_token()
    return u


@pytest.fixture()
def seeded(db_session):
    return BadgeService.seed_defaults()


def _ub_count(user_id: int, badge_key: str, workspace_id=None) -> int:
    from backend.models.badge import Badge

    badge = db.session.scalar(select(Badge).where(Badge.key == badge_key))
    if badge is None:
        return 0
    stmt = select(func.count(UserBadge.id)).where(
        UserBadge.user_id == user_id,
        UserBadge.badge_id == badge.id,
    )
    if workspace_id is None:
        stmt = stmt.where(UserBadge.workspace_id.is_(None))
    else:
        stmt = stmt.where(UserBadge.workspace_id == workspace_id)
    return db.session.scalar(stmt) or 0


class TestPublicScopeIdempotency:
    def test_double_award_no_duplicate(self, user, seeded, db_session):
        BadgeService.award(user.id, "first_accepted_revision")
        BadgeService.award(user.id, "first_accepted_revision")
        assert _ub_count(user.id, "first_accepted_revision") == 1

    def test_triple_award_no_duplicate(self, user, seeded, db_session):
        for _ in range(3):
            BadgeService.award(user.id, "prolific_author")
        assert _ub_count(user.id, "prolific_author") == 1

    def test_second_award_returns_none(self, user, seeded, db_session):
        BadgeService.award(user.id, "helpful_commenter")
        result = BadgeService.award(user.id, "helpful_commenter")
        assert result is None

    def test_has_badge_consistent_after_double_award(self, user, seeded, db_session):
        BadgeService.award(user.id, "first_post")
        BadgeService.award(user.id, "first_post")
        assert BadgeService.has_badge(user.id, "first_post") is True


class TestWorkspaceScopeIdempotency:
    def test_workspace_double_award_no_duplicate(self, user, seeded, db_session):
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws.id)
        BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws.id)
        assert _ub_count(user.id, "first_accepted_revision", workspace_id=ws.id) == 1

    def test_check_contribution_idempotent(self, user, seeded, db_session):
        """check_contribution_badges called twice produces no duplicate rows."""
        from unittest.mock import patch

        summary = {
            "revisions_accepted": 1,
            "benchmarks_run": 0,
            "ab_experiments_created": 0,
        }
        streak = {"current_streak": 0, "longest_streak": 0}
        with (
            patch(
                "backend.services.badge_service._count_ontology_nodes",
                return_value=0,
            ),
            patch(
                "backend.services.badge_service._count_ab_wins",
                return_value=0,
            ),
            patch(
                "backend.services.badge_service._count_received_upvotes",
                return_value=0,
            ),
        ):
            from backend.services import user_analytics_service as uas

            with (
                patch.object(
                    uas, "build_user_contribution_summary", return_value=summary
                ),
                patch.object(uas, "compute_contribution_streak", return_value=streak),
            ):
                BadgeService.check_contribution_badges(user.id)
                BadgeService.check_contribution_badges(user.id)

        assert _ub_count(user.id, "first_revision") == 1
