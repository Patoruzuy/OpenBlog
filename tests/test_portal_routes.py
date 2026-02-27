"""Tests for settings routes, repository service, AuthService password helpers,
and the public profile privacy gate.

Covers
------
* AuthService.verify_password / change_password
* RepositoryService — CRUD + reorder + ownership check
* /settings/* route auth guards + happy paths
* /users/<username> privacy gate
"""

from __future__ import annotations

import json

import pytest

from backend.extensions import db
from backend.services.auth_service import AuthError, AuthService
from backend.services.privacy_service import PrivacyService
from backend.services.repository_service import RepositoryService, RepositoryServiceError


# ─────────────────────────────────────────────────────────────────────────────
# Per-test user fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token):
    user, _ = make_user_token("alice@routes.com", "routes_alice")
    return user


@pytest.fixture()
def alice_token(make_user_token):
    user, token = make_user_token("alicet@routes.com", "routes_alicet")
    return user, token


@pytest.fixture()
def bob(make_user_token):
    user, _ = make_user_token("bob@routes.com", "routes_bob")
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _login(client, user_id: int) -> None:
    """Inject a session cookie to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# ─────────────────────────────────────────────────────────────────────────────
# AuthService — password helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestPasswordHelpers:
    def test_verify_correct_password(self, db_session, alice):
        assert AuthService.verify_password(alice, "StrongPass123!!") is True

    def test_verify_wrong_password(self, db_session, alice):
        assert AuthService.verify_password(alice, "WrongPass999!!") is False

    def test_change_password_updates_hash(self, db_session, alice):
        AuthService.change_password(alice, "NewPassword456!!")
        assert AuthService.verify_password(alice, "NewPassword456!!") is True
        assert AuthService.verify_password(alice, "StrongPass123!!") is False

    def test_change_password_too_short_raises(self, db_session, alice):
        with pytest.raises(AuthError, match="15"):
            AuthService.change_password(alice, "Short1!")


# ─────────────────────────────────────────────────────────────────────────────
# RepositoryService
# ─────────────────────────────────────────────────────────────────────────────


class TestRepositoryService:
    def test_add_repository(self, db_session, alice):
        repo = RepositoryService.add(
            alice,
            repo_name="My Project",
            repo_url="https://github.com/me/project",
            description="A test project",
            language="Python",
            is_featured=True,
            is_public=True,
        )
        assert repo.id is not None
        assert repo.user_id  == alice.id
        assert repo.repo_name == "My Project"

    def test_add_duplicate_url_raises(self, db_session, alice):
        RepositoryService.add(alice, repo_name="Dup Repo", repo_url="https://github.com/me/dup")
        with pytest.raises(RepositoryServiceError, match="already added"):
            RepositoryService.add(alice, repo_name="Dup Repo", repo_url="https://github.com/me/dup")

    def test_get_for_user_returns_all_items(self, db_session, alice):
        RepositoryService.add(alice, repo_name="Repo A", repo_url="https://github.com/me/a")
        RepositoryService.add(alice, repo_name="Repo B", repo_url="https://github.com/me/b")
        assert len(RepositoryService.get_for_user(alice.id)) == 2

    def test_get_for_user_public_only_filters(self, db_session, alice):
        RepositoryService.add(alice, repo_name="Pub Repo", repo_url="https://github.com/me/pub", is_public=True)
        RepositoryService.add(alice, repo_name="Priv Repo", repo_url="https://github.com/me/priv", is_public=False)
        repos = RepositoryService.get_for_user(alice.id, public_only=True)
        assert len(repos) == 1
        assert repos[0].is_public is True

    def test_update_repository(self, db_session, alice):
        repo = RepositoryService.add(alice, repo_name="Upd Repo", repo_url="https://github.com/me/upd")
        updated = RepositoryService.update(repo.id, alice.id, description="updated")
        assert updated.description == "updated"

    def test_delete_repository(self, db_session, alice):
        repo = RepositoryService.add(alice, repo_name="Del Repo", repo_url="https://github.com/me/del")
        RepositoryService.delete(repo.id, alice.id)
        assert RepositoryService.get_for_user(alice.id) == []

    def test_get_by_id_wrong_user_raises(self, db_session, alice, bob):
        repo = RepositoryService.add(alice, repo_name="Alice Repo", repo_url="https://github.com/alice/x")
        with pytest.raises(RepositoryServiceError, match="[Nn]ot found"):
            RepositoryService.get_by_id(repo.id, bob.id)

    def test_reorder_repositories(self, db_session, alice):
        r1 = RepositoryService.add(alice, repo_name="Repo 1", repo_url="https://github.com/me/r1")
        r2 = RepositoryService.add(alice, repo_name="Repo 2", repo_url="https://github.com/me/r2")
        r3 = RepositoryService.add(alice, repo_name="Repo 3", repo_url="https://github.com/me/r3")
        RepositoryService.reorder(alice.id, [r3.id, r1.id, r2.id])
        repos = RepositoryService.get_for_user(alice.id)
        assert [r.id for r in repos] == [r3.id, r1.id, r2.id]


# ─────────────────────────────────────────────────────────────────────────────
# Settings routes — auth guard
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsRequiresAuth:
    @pytest.mark.parametrize("path", [
        "/settings/profile",
        "/settings/privacy",
        "/settings/security",
        "/settings/accounts",
        "/settings/repositories",
        "/settings/contributions",
    ])
    def test_redirects_to_login_when_unauthenticated(self, auth_client, db_session, path):
        resp = auth_client.get(path, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/auth/login" in resp.headers["Location"]


# ─────────────────────────────────────────────────────────────────────────────
# Settings routes — profile
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsProfile:
    def test_get_returns_200(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/settings/profile")
        assert resp.status_code == 200

    def test_post_updates_headline(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/profile",
            data={"headline": "Lead Developer"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(alice)
        assert alice.headline == "Lead Developer"

    def test_post_rejects_overlong_headline(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/profile",
            data={"headline": "x" * 201},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"200 characters" in resp.data


# ─────────────────────────────────────────────────────────────────────────────
# Settings routes — privacy
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsPrivacy:
    def test_get_returns_200(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/settings/privacy")
        assert resp.status_code == 200

    def test_post_sets_members_visibility(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/privacy",
            data={"profile_visibility": "members"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        ps = PrivacyService.get_or_create_privacy(alice)
        assert ps.profile_visibility == "members"

    def test_post_sets_pseudonymous_alias(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/privacy",
            data={
                "default_identity_mode": "pseudonymous",
                "pseudonymous_alias":    "dark_coder",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        ps = PrivacyService.get_or_create_privacy(alice)
        assert ps.pseudonymous_alias == "dark_coder"


# ─────────────────────────────────────────────────────────────────────────────
# Settings routes — security / password change
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsSecurity:
    def test_get_returns_200(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/settings/security")
        assert resp.status_code == 200

    def test_change_password_succeeds(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/security/password",
            data={
                "current_password": "StrongPass123!!",
                "new_password":     "NewStrongPass456!!",
                "confirm_password": "NewStrongPass456!!",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(alice)
        assert AuthService.verify_password(alice, "NewStrongPass456!!") is True

    def test_change_password_wrong_current_fails(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/security/password",
            data={
                "current_password": "WrongPassword123!!",
                "new_password":     "NewStrongPass456!!",
                "confirm_password": "NewStrongPass456!!",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        data_lower = resp.data.lower()
        assert b"current password" in data_lower or b"incorrect" in data_lower

    def test_change_password_mismatch_fails(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/security/password",
            data={
                "current_password": "StrongPass123!!",
                "new_password":     "NewStrongPass456!!",
                "confirm_password": "DifferentPass789!!",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        data_lower = resp.data.lower()
        assert b"do not match" in data_lower or b"passwords must match" in data_lower


# ─────────────────────────────────────────────────────────────────────────────
# Settings routes — repositories
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsRepositories:
    def test_get_returns_200(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/settings/repositories")
        assert resp.status_code == 200

    def test_add_repository(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            "/settings/repositories/add",
            data={
                "repo_name": "Awesome Project",
                "repo_url":  "https://github.com/me/awesome",
                "language":  "Python",
                "is_public": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        repos = RepositoryService.get_for_user(alice.id)
        assert len(repos) == 1
        assert repos[0].repo_name == "Awesome Project"

    def test_reorder_endpoint(self, auth_client, alice):
        _login(auth_client, alice.id)
        r1 = RepositoryService.add(alice, repo_name="Ro 1", repo_url="https://github.com/me/ro1")
        r2 = RepositoryService.add(alice, repo_name="Ro 2", repo_url="https://github.com/me/ro2")
        resp = auth_client.post(
            "/settings/repositories/reorder",
            data=json.dumps({"order": [r2.id, r1.id]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert json.loads(resp.data).get("ok") is True

    def test_delete_repository(self, auth_client, alice):
        _login(auth_client, alice.id)
        repo = RepositoryService.add(alice, repo_name="To Drop", repo_url="https://github.com/me/todrop")
        resp = auth_client.post(
            f"/settings/repositories/{repo.id}/delete",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert RepositoryService.get_for_user(alice.id) == []


# ─────────────────────────────────────────────────────────────────────────────
# Public profile — privacy gate
# ─────────────────────────────────────────────────────────────────────────────


class TestPublicProfilePrivacyGate:
    def test_public_profile_returns_200(self, auth_client, alice):
        resp = auth_client.get(f"/users/{alice.username}")
        assert resp.status_code == 200

    def test_private_profile_shows_unavailable_stub(self, auth_client, alice):
        ps = PrivacyService.get_or_create_privacy(alice)
        ps.profile_visibility = "private"
        db.session.commit()
        resp = auth_client.get(f"/users/{alice.username}")
        assert resp.status_code == 200
        data_lower = resp.data.lower()
        assert b"unavailable" in data_lower or b"private" in data_lower

    def test_private_profile_visible_to_owner(self, auth_client, alice):
        ps = PrivacyService.get_or_create_privacy(alice)
        ps.profile_visibility = "private"
        db.session.commit()
        _login(auth_client, alice.id)
        resp = auth_client.get(f"/users/{alice.username}")
        assert resp.status_code == 200
        assert b"profile-hidden" not in resp.data

    def test_members_only_hidden_from_anonymous(self, auth_client, alice):
        ps = PrivacyService.get_or_create_privacy(alice)
        ps.profile_visibility = "members"
        db.session.commit()
        resp = auth_client.get(f"/users/{alice.username}")
        assert resp.status_code == 200
        data_lower = resp.data.lower()
        assert b"unavailable" in data_lower or b"members" in data_lower

    def test_members_only_visible_to_logged_in_user(self, auth_client, alice, bob):
        ps = PrivacyService.get_or_create_privacy(alice)
        ps.profile_visibility = "members"
        db.session.commit()
        _login(auth_client, bob.id)
        resp = auth_client.get(f"/users/{alice.username}")
        assert resp.status_code == 200
        assert b"profile-hidden" not in resp.data
