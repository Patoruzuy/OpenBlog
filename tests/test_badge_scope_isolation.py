"""Tests for badge scope isolation — workspace badges never leak publicly."""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db
from backend.services.badge_service import BadgeService

_ctr = itertools.count(200)


def _make_workspace(owner_id: int) -> object:
    from backend.models.workspace import Workspace  # noqa: PLC0415

    n = next(_ctr)
    ws = Workspace(name=f"Iso {n}", slug=f"iso-{n}", owner_id=owner_id)
    db.session.add(ws)
    db.session.flush()
    return ws


@pytest.fixture()
def user(make_user_token):
    u, _ = make_user_token()
    return u


@pytest.fixture(autouse=True)
def seeded(db_session):
    BadgeService.seed_defaults()


class TestListForUserScopeIsolation:
    def test_public_badge_not_in_workspace_only_query(self, user, db_session):
        """A public badge (workspace=None) does NOT appear when public_only=True
        if cross-scope leakage exists."""
        BadgeService.award(user.id, "first_accepted_revision")
        public_items = BadgeService.list_for_user(user.id, public_only=True)
        keys = {ub.badge.key for ub in public_items}
        assert "first_accepted_revision" in keys

    def test_workspace_badge_excluded_from_public_only(self, user, db_session):
        """A workspace-scoped badge must NOT appear when public_only=True."""
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws.id)
        public_items = BadgeService.list_for_user(user.id, public_only=True)
        keys = {ub.badge.key for ub in public_items}
        assert "first_accepted_revision" not in keys

    def test_workspace_badge_visible_in_workspace_view(self, user, db_session):
        """Workspace-scoped badges ARE included when viewing own workspace."""
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "ten_revisions", workspace_id=ws.id)
        ws_items = BadgeService.list_for_user(user.id, workspace_id=ws.id)
        keys = {ub.badge.key for ub in ws_items}
        assert "ten_revisions" in keys

    def test_both_scopes_visible_to_self(self, user, db_session):
        """Self view (public_only=False, no workspace filter) sees all badges."""
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision")
        BadgeService.award(user.id, "ten_revisions", workspace_id=ws.id)
        all_items = BadgeService.list_for_user(user.id)
        keys = {ub.badge.key for ub in all_items}
        assert "first_accepted_revision" in keys
        assert "ten_revisions" in keys

    def test_workspace_a_does_not_see_workspace_b_badges(self, user, db_session):
        """Badges from workspace 1 are not visible when querying for workspace 2."""
        ws1 = _make_workspace(user.id)
        ws2 = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws1.id)
        ws2_items = BadgeService.list_for_user(user.id, workspace_id=ws2.id)
        ws1_only = [ub for ub in ws2_items if ub.workspace_id == ws1.id]
        assert len(ws1_only) == 0

    def test_multiple_users_badges_isolated(self, make_user_token, db_session):
        """User A's badges never appear in user B's badge list."""
        user_a, _ = make_user_token()
        user_b, _ = make_user_token()
        BadgeService.award(user_a.id, "first_accepted_revision")
        b_badges = BadgeService.list_for_user(user_b.id)
        assert len(b_badges) == 0


class TestHasBadgeScopeIsolation:
    def test_public_badge_not_found_in_workspace_scope(self, user, db_session):
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision")
        # Public badge should not be considered as workspace badge
        assert (
            BadgeService.has_badge(
                user.id, "first_accepted_revision", workspace_id=ws.id
            )
            is False
        )

    def test_workspace_badge_not_found_in_public_scope(self, user, db_session):
        ws = _make_workspace(user.id)
        BadgeService.award(user.id, "first_accepted_revision", workspace_id=ws.id)
        # Workspace badge should not be visible as public badge
        assert BadgeService.has_badge(user.id, "first_accepted_revision") is False
