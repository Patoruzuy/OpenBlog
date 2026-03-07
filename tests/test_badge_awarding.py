"""Tests for BadgeService.award() — workspace-aware awarding."""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db
from backend.models.badge import UserBadge
from backend.services.badge_service import BadgeError, BadgeService

_ctr = itertools.count(1)


def _make_workspace(owner_id: int) -> object:
    from backend.models.workspace import Workspace  # noqa: PLC0415

    n = next(_ctr)
    ws = Workspace(name=f"WS {n}", slug=f"ws-{n}", owner_id=owner_id)
    db.session.add(ws)
    db.session.flush()
    return ws


@pytest.fixture()
def user(make_user_token):
    u, _ = make_user_token()
    return u


@pytest.fixture()
def other_user(make_user_token):
    u, _ = make_user_token()
    return u


@pytest.fixture()
def seeded(db_session):
    return BadgeService.seed_defaults()


# -- Public scope award -------------------------------------------------------


class TestPublicAward:
    def test_award_returns_user_badge(self, user, seeded, db_session):
        ub = BadgeService.award(user.id, "first_accepted_revision")
        assert ub is not None
        assert isinstance(ub, UserBadge)
        assert ub.user_id == user.id
        assert ub.workspace_id is None

    def test_award_sets_awarded_at(self, user, seeded, db_session):
        ub = BadgeService.award(user.id, "first_accepted_revision")
        assert ub is not None
        assert ub.awarded_at is not None

    def test_award_unknown_user_raises(self, seeded, db_session):
        with pytest.raises(BadgeError) as exc_info:
            BadgeService.award(999_999, "first_accepted_revision")
        assert exc_info.value.status_code == 404

    def test_award_unknown_key_raises(self, user, db_session):
        with pytest.raises(BadgeError) as exc_info:
            BadgeService.award(user.id, "nonexistent_key_xyz")
        assert exc_info.value.status_code == 404

    def test_award_different_users_same_key(self, user, other_user, seeded, db_session):
        ub1 = BadgeService.award(user.id, "first_accepted_revision")
        ub2 = BadgeService.award(other_user.id, "first_accepted_revision")
        assert ub1 is not None
        assert ub2 is not None
        assert ub1.user_id != ub2.user_id


# -- Workspace scope award ----------------------------------------------------


class TestWorkspaceAward:
    def test_award_workspace_scoped(self, user, seeded, db_session):
        ws = _make_workspace(user.id)
        ub = BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws.id)
        assert ub is not None
        assert ub.workspace_id == ws.id

    def test_award_same_badge_different_scopes(self, user, seeded, db_session):
        """Same badge can be awarded in public scope and workspace scope."""
        ws = _make_workspace(user.id)
        public_ub = BadgeService.award(user.id, "first_accepted_revision")
        ws_ub = BadgeService.award(
            user.id, "first_accepted_revision", workspace_id=ws.id
        )
        assert public_ub is not None
        assert ws_ub is not None
        assert public_ub.workspace_id is None
        assert ws_ub.workspace_id == ws.id

    def test_award_different_workspaces_same_badge(self, user, seeded, db_session):
        """Same badge awarded in ws=1 and ws=2 both succeed."""
        ws1 = _make_workspace(user.id)
        ws2 = _make_workspace(user.id)
        ub1 = BadgeService.award(
            user.id, "first_accepted_revision", workspace_id=ws1.id
        )
        ub2 = BadgeService.award(
            user.id, "first_accepted_revision", workspace_id=ws2.id
        )
        assert ub1 is not None
        assert ub2 is not None
        assert ub1.workspace_id == ws1.id
        assert ub2.workspace_id == ws2.id

    def test_award_returns_none_if_already_in_workspace(self, user, seeded, db_session):
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws.id)
        second = BadgeService.award(
            user.id, "first_accepted_revision", workspace_id=ws.id
        )
        assert second is None


# -- has_badge ----------------------------------------------------------------


class TestHasBadge:
    def test_has_badge_true_after_award(self, user, seeded, db_session):
        BadgeService.award(user.id, "first_accepted_revision")
        assert BadgeService.has_badge(user.id, "first_accepted_revision") is True

    def test_has_badge_false_before_award(self, user, seeded, db_session):
        assert BadgeService.has_badge(user.id, "first_accepted_revision") is False

    def test_has_badge_scope_aware(self, user, seeded, db_session):
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws.id)
        # Not in public scope
        assert BadgeService.has_badge(user.id, "first_accepted_revision") is False
        # In the workspace scope
        assert (
            BadgeService.has_badge(
                user.id, "first_accepted_revision", workspace_id=ws.id
            )
            is True
        )

    def test_has_badge_unknown_key_returns_false(self, user, db_session):
        assert BadgeService.has_badge(user.id, "no_such_badge") is False
