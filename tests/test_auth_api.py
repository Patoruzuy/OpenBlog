"""Auth API endpoint tests.

Uses the ``auth_client`` fixture which provides:
  - a Flask test client
  - all DB tables created in SQLite in-memory
  - Redis replaced with _FakeRedis (defined in conftest.py)

All rate limits are disabled in TestingConfig (RATELIMIT_ENABLED = False).
"""

from __future__ import annotations

import pytest

# ── /api/auth/register ────────────────────────────────────────────────────────


class TestRegister:
    def test_success_returns_201_and_tokens(self, auth_client):
        resp = auth_client.post(
            "/api/auth/register",
            json={"email": "new@example.com", "username": "newuser", "password": "StrongPass123!!"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "Bearer"
        assert data["user"]["email"] == "new@example.com"
        assert data["user"]["role"] == "reader"

    def test_display_name_optional(self, auth_client):
        resp = auth_client.post(
            "/api/auth/register",
            json={
                "email": "dn@example.com",
                "username": "dnuser",
                "password": "StrongPass123!!",
                "display_name": "Display Name",
            },
        )
        assert resp.status_code == 201

    def test_missing_email_returns_400(self, auth_client):
        resp = auth_client.post(
            "/api/auth/register", json={"username": "u", "password": "StrongPass123!!"}
        )
        assert resp.status_code == 400

    def test_missing_username_returns_400(self, auth_client):
        resp = auth_client.post(
            "/api/auth/register", json={"email": "x@y.com", "password": "StrongPass123!!"}
        )
        assert resp.status_code == 400

    def test_missing_password_returns_400(self, auth_client):
        resp = auth_client.post(
            "/api/auth/register", json={"email": "x@y.com", "username": "u"}
        )
        assert resp.status_code == 400

    def test_short_password_returns_400(self, auth_client):
        resp = auth_client.post(
            "/api/auth/register",
            json={"email": "short@example.com", "username": "shortpw", "password": "abc"},
        )
        assert resp.status_code == 400
        assert "15 characters" in resp.get_json()["error"]

    def test_duplicate_email_returns_409(self, auth_client):
        payload = {"email": "dup@example.com", "username": "user1", "password": "StrongPass123!!"}
        auth_client.post("/api/auth/register", json=payload)
        payload["username"] = "user2"
        resp = auth_client.post("/api/auth/register", json=payload)
        assert resp.status_code == 409

    def test_duplicate_username_returns_409(self, auth_client):
        auth_client.post(
            "/api/auth/register",
            json={"email": "a@example.com", "username": "sameuser", "password": "StrongPass123!!"},
        )
        resp = auth_client.post(
            "/api/auth/register",
            json={"email": "b@example.com", "username": "sameuser", "password": "StrongPass123!!"},
        )
        assert resp.status_code == 409


# ── /api/auth/login ───────────────────────────────────────────────────────────


class TestLogin:
    @pytest.fixture(autouse=True)
    def _create_user(self, auth_client):
        """Register a user once for all login tests in this class."""
        auth_client.post(
            "/api/auth/register",
            json={
                "email": "login@example.com",
                "username": "loginuser",
                "password": "StrongPass123!!",
            },
        )

    def test_success_returns_tokens(self, auth_client):
        resp = auth_client.post(
            "/api/auth/login",
            json={"email": "login@example.com", "password": "StrongPass123!!"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["username"] == "loginuser"

    def test_email_is_case_insensitive(self, auth_client):
        resp = auth_client.post(
            "/api/auth/login",
            json={"email": "LOGIN@EXAMPLE.COM", "password": "StrongPass123!!"},
        )
        assert resp.status_code == 200

    def test_wrong_password_returns_401(self, auth_client):
        resp = auth_client.post(
            "/api/auth/login",
            json={"email": "login@example.com", "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    def test_unknown_email_returns_401(self, auth_client):
        resp = auth_client.post(
            "/api/auth/login",
            json={"email": "ghost@example.com", "password": "anything"},
        )
        assert resp.status_code == 401

    def test_missing_email_returns_400(self, auth_client):
        resp = auth_client.post("/api/auth/login", json={"password": "StrongPass123!!"})
        assert resp.status_code == 400

    def test_missing_password_returns_400(self, auth_client):
        resp = auth_client.post("/api/auth/login", json={"email": "login@example.com"})
        assert resp.status_code == 400

    def test_empty_body_returns_400(self, auth_client):
        resp = auth_client.post("/api/auth/login", json={})
        assert resp.status_code == 400


# ── /api/auth/refresh & /logout ───────────────────────────────────────────────


class TestRefreshAndLogout:
    @pytest.fixture
    def tokens(self, auth_client):
        """Register + login, return the token dict."""
        auth_client.post(
            "/api/auth/register",
            json={"email": "rt@example.com", "username": "rtuser", "password": "StrongPass123!!"},
        )
        resp = auth_client.post(
            "/api/auth/login",
            json={"email": "rt@example.com", "password": "StrongPass123!!"},
        )
        return resp.get_json()

    def test_refresh_returns_new_token_pair(self, auth_client, tokens):
        resp = auth_client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_refresh_tokens_differ_from_originals(self, auth_client, tokens):
        resp = auth_client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        new_data = resp.get_json()
        assert new_data["refresh_token"] != tokens["refresh_token"]
        assert new_data["access_token"] != tokens["access_token"]

    def test_refresh_token_single_use(self, auth_client, tokens):
        """Reusing the same refresh token should fail on the second call."""
        old_rt = tokens["refresh_token"]
        # First use: succeeds.
        resp1 = auth_client.post("/api/auth/refresh", json={"refresh_token": old_rt})
        assert resp1.status_code == 200
        # Second use of the same (now-revoked) token: fails.
        resp2 = auth_client.post("/api/auth/refresh", json={"refresh_token": old_rt})
        assert resp2.status_code == 401

    def test_refresh_missing_token_returns_400(self, auth_client):
        resp = auth_client.post("/api/auth/refresh", json={})
        assert resp.status_code == 400

    def test_logout_returns_200(self, auth_client, tokens):
        resp = auth_client.post(
            "/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert resp.status_code == 200
        assert "Logged out" in resp.get_json()["message"]

    def test_logout_then_refresh_rejected(self, auth_client, tokens):
        rt = tokens["refresh_token"]
        auth_client.post("/api/auth/logout", json={"refresh_token": rt})
        resp = auth_client.post("/api/auth/refresh", json={"refresh_token": rt})
        assert resp.status_code == 401

    def test_logout_idempotent(self, auth_client, tokens):
        """Logging out twice should not raise an error."""
        rt = tokens["refresh_token"]
        auth_client.post("/api/auth/logout", json={"refresh_token": rt})
        resp = auth_client.post("/api/auth/logout", json={"refresh_token": rt})
        assert resp.status_code == 200


# ── /api/auth/me ──────────────────────────────────────────────────────────────


class TestMe:
    @pytest.fixture
    def access_token(self, auth_client):
        auth_client.post(
            "/api/auth/register",
            json={"email": "me@example.com", "username": "meuser", "password": "StrongPass123!!"},
        )
        resp = auth_client.post(
            "/api/auth/login",
            json={"email": "me@example.com", "password": "StrongPass123!!"},
        )
        return resp.get_json()["access_token"]

    def test_me_returns_user_profile(self, auth_client, access_token):
        resp = auth_client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["username"] == "meuser"
        assert data["role"] == "reader"
        assert "reputation_score" in data

    def test_me_no_token_returns_401(self, auth_client):
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_invalid_token_returns_401(self, auth_client):
        resp = auth_client.get(
            "/api/auth/me", headers={"Authorization": "Bearer not.a.token"}
        )
        assert resp.status_code == 401

    def test_me_wrong_scheme_returns_401(self, auth_client):
        resp = auth_client.get("/api/auth/me", headers={"Authorization": "Basic abc123"})
        assert resp.status_code == 401
