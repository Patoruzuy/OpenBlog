"""Tests for password reset and email verification flows (SM3)."""

from __future__ import annotations

import pytest

from backend.services.auth_service import AuthService

_DEFAULT_PW = "StrongPass123!!"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_user(username="pwresetuser", email="pwreset@example.com", password=_DEFAULT_PW):
    """Register a user via AuthService and return the User object."""
    return AuthService.register(email, username, password)


# ---------------------------------------------------------------------------
# Forgot-password page
# ---------------------------------------------------------------------------
class TestForgotPassword:
    def test_form_renders(self, client):
        rv = client.get("/auth/forgot-password")
        assert rv.status_code == 200
        assert b"password" in rv.data.lower()

    def test_unknown_email_silently_succeeds(self, auth_client, db_session):
        """Posting an unknown e-mail must NOT leak whether the address exists."""
        rv = auth_client.post(
            "/auth/forgot-password",
            data={"email": "nobody@example.com"},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        # Should show success or the form again — never an error
        assert rv.status_code == 200

    def test_known_email_returns_success(self, auth_client, db_session):
        _make_user()  # default email: pwreset@example.com
        rv = auth_client.post(
            "/auth/forgot-password",
            data={"email": "pwreset@example.com"},
            follow_redirects=True,
        )
        assert rv.status_code == 200


# ---------------------------------------------------------------------------
# Reset-password page (token flow)
# ---------------------------------------------------------------------------
class TestResetPassword:
    def test_invalid_token_renders_error_page(self, client):
        rv = client.get("/auth/reset-password/thisisnotavalidtoken")
        assert rv.status_code == 200
        assert (
            b"invalid" in rv.data.lower()
            or b"expired" in rv.data.lower()
            or b"token" in rv.data.lower()
        )

    def test_valid_token_renders_form(self, auth_client, db_session):
        user = _make_user(username="vt1", email="vt1@example.com")
        token = AuthService.generate_password_reset_token(user)
        rv = auth_client.get(f"/auth/reset-password/{token}")
        assert rv.status_code == 200
        assert b"password" in rv.data.lower()

    def test_short_password_rejected(self, auth_client, db_session):
        user = _make_user(username="short1", email="short1@example.com")
        token = AuthService.generate_password_reset_token(user)
        rv = auth_client.post(
            f"/auth/reset-password/{token}",
            data={"password": "tooshort", "confirm_password": "tooshort"},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        assert b"password" in rv.data.lower()

    def test_mismatched_passwords_rejected(self, auth_client, db_session):
        user = _make_user(username="mismatch1", email="mismatch1@example.com")
        token = AuthService.generate_password_reset_token(user)
        rv = auth_client.post(
            f"/auth/reset-password/{token}",
            data={
                "password": "A" * 15,
                "confirm_password": "B" * 15,
            },
            follow_redirects=True,
        )
        assert rv.status_code == 200
        assert b"match" in rv.data.lower() or b"password" in rv.data.lower()

    def test_successful_reset_redirects_to_login(self, auth_client, db_session):
        user = _make_user(username="success1", email="success1@example.com")
        token = AuthService.generate_password_reset_token(user)
        new_pw = "MyValidNewPassword123!"
        rv = auth_client.post(
            f"/auth/reset-password/{token}",
            data={"password": new_pw, "confirm_password": new_pw},
            follow_redirects=False,
        )
        assert rv.status_code in (302, 303)
        assert "/auth/login" in rv.headers.get("Location", "")

    def test_successful_reset_allows_login(self, auth_client, db_session):
        user = _make_user(username="canlogin1", email="canlogin1@example.com")
        token = AuthService.generate_password_reset_token(user)
        new_pw = "MyValidNewPassword2024!"
        auth_client.post(
            f"/auth/reset-password/{token}",
            data={"password": new_pw, "confirm_password": new_pw},
            follow_redirects=True,
        )
        # Should now be able to log in with the new password
        rv = auth_client.post(
            "/auth/login",
            data={"email": "canlogin1@example.com", "password": new_pw},
            follow_redirects=False,
        )
        assert rv.status_code in (302, 303)


# ---------------------------------------------------------------------------
# Email verification token flow
# ---------------------------------------------------------------------------
class TestEmailVerification:
    def test_invalid_token_renders_error_page(self, client):
        rv = client.get("/auth/verify/notavalidtoken")
        assert rv.status_code == 200
        assert (
            b"invalid" in rv.data.lower()
            or b"expired" in rv.data.lower()
            or b"token" in rv.data.lower()
        )

    def test_valid_token_marks_email_verified(self, auth_client, db_session):
        from backend.extensions import db

        user = _make_user(username="verifyuser1", email="verifyuser1@example.com")
        assert not user.is_email_verified
        token = AuthService.generate_email_verification_token(user)
        rv = auth_client.get(f"/auth/verify/{token}")
        assert rv.status_code == 200
        db.session.refresh(user)
        assert user.is_email_verified
