"""Tests for the badges API endpoints."""

from __future__ import annotations

import pytest

from backend.services.badge_service import BadgeService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def admin(make_user_token):
    user, tok = make_user_token("admin@example.com", "admin", role="admin")
    return user, tok


@pytest.fixture()
def regular_user(make_user_token):
    user, tok = make_user_token("user@example.com", "regularuser")
    return user, tok


@pytest.fixture()
def seeded(db_session):
    return BadgeService.seed_defaults()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── GET /api/badges/ ──────────────────────────────────────────────────────────


class TestListBadgeDefinitions:
    def test_returns_empty_list_when_no_badges(self, auth_client, db_session):
        resp = auth_client.get("/api/badges/")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_seeded_badges(self, auth_client, seeded, db_session):
        resp = auth_client.get("/api/badges/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 4
        keys = {item["key"] for item in data}
        assert "first_accepted_revision" in keys
        assert "prolific_author" in keys

    def test_badge_dict_shape(self, auth_client, seeded, db_session):
        resp = auth_client.get("/api/badges/")
        item = resp.get_json()[0]
        assert "key" in item
        assert "name" in item
        assert "description" in item
        assert "icon_url" in item

    def test_public_no_auth_required(self, auth_client, seeded, db_session):
        resp = auth_client.get("/api/badges/")
        assert resp.status_code == 200


# ── GET /api/users/<username>/badges ─────────────────────────────────────────


class TestListUserBadges:
    def test_returns_empty_when_no_badges(self, auth_client, regular_user, seeded):
        user, _ = regular_user
        resp = auth_client.get(f"/api/users/{user.username}/badges")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_awarded_badges(self, auth_client, regular_user, seeded, db_session):
        user, _ = regular_user
        BadgeService.award(user.id, "first_accepted_revision")
        resp = auth_client.get(f"/api/users/{user.username}/badges")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["badge"]["key"] == "first_accepted_revision"

    def test_user_badge_dict_shape(self, auth_client, regular_user, seeded, db_session):
        user, _ = regular_user
        BadgeService.award(user.id, "prolific_author")
        resp = auth_client.get(f"/api/users/{user.username}/badges")
        item = resp.get_json()[0]
        assert "badge" in item
        assert "awarded_at" in item

    def test_unknown_user_returns_404(self, auth_client, db_session):
        resp = auth_client.get("/api/users/nobody_here/badges")
        assert resp.status_code == 404

    def test_public_no_auth_required(self, auth_client, regular_user, seeded):
        user, _ = regular_user
        resp = auth_client.get(f"/api/users/{user.username}/badges")
        assert resp.status_code == 200


# ── POST /api/users/<username>/badges ────────────────────────────────────────


class TestAwardBadge:
    def test_admin_can_award_badge(self, auth_client, admin, regular_user, seeded, db_session):
        _, admin_tok = admin
        user, _ = regular_user
        resp = auth_client.post(
            f"/api/users/{user.username}/badges",
            json={"badge_key": "prolific_author"},
            headers=_h(admin_tok),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["badge"]["key"] == "prolific_author"
        assert "awarded_at" in data

    def test_award_idempotent_returns_200(
        self, auth_client, admin, regular_user, seeded, db_session
    ):
        _, admin_tok = admin
        user, _ = regular_user
        # First award → 201
        auth_client.post(
            f"/api/users/{user.username}/badges",
            json={"badge_key": "helpful_commenter"},
            headers=_h(admin_tok),
        )
        # Second award (idempotent) → 200 with existing record
        resp = auth_client.post(
            f"/api/users/{user.username}/badges",
            json={"badge_key": "helpful_commenter"},
            headers=_h(admin_tok),
        )
        assert resp.status_code == 200
        assert resp.get_json()["badge"]["key"] == "helpful_commenter"

    def test_non_admin_gets_403(self, auth_client, regular_user, seeded, db_session):
        user, tok = regular_user
        resp = auth_client.post(
            f"/api/users/{user.username}/badges",
            json={"badge_key": "prolific_author"},
            headers=_h(tok),
        )
        assert resp.status_code == 403

    def test_unauthenticated_gets_401(self, auth_client, regular_user, seeded, db_session):
        user, _ = regular_user
        resp = auth_client.post(
            f"/api/users/{user.username}/badges",
            json={"badge_key": "prolific_author"},
        )
        assert resp.status_code == 401

    def test_missing_badge_key_returns_400(
        self, auth_client, admin, regular_user, seeded, db_session
    ):
        _, admin_tok = admin
        user, _ = regular_user
        resp = auth_client.post(
            f"/api/users/{user.username}/badges",
            json={},
            headers=_h(admin_tok),
        )
        assert resp.status_code == 400

    def test_unknown_badge_key_returns_404(
        self, auth_client, admin, regular_user, seeded, db_session
    ):
        _, admin_tok = admin
        user, _ = regular_user
        resp = auth_client.post(
            f"/api/users/{user.username}/badges",
            json={"badge_key": "completely_fake_badge"},
            headers=_h(admin_tok),
        )
        assert resp.status_code == 404

    def test_unknown_user_returns_404(self, auth_client, admin, seeded, db_session):
        _, admin_tok = admin
        resp = auth_client.post(
            "/api/users/ghost_user/badges",
            json={"badge_key": "prolific_author"},
            headers=_h(admin_tok),
        )
        assert resp.status_code == 404
