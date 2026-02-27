"""JSON API — authentication endpoints.

All endpoints are CSRF-exempt (the JWT / Bearer-token scheme is the protection
mechanism for API clients).  Rate limits are applied per-IP via Flask-Limiter.

Routes
------
POST /api/auth/register   create account → token pair
POST /api/auth/login      verify credentials → token pair
POST /api/auth/refresh    rotate refresh token → new pair
POST /api/auth/logout     revoke refresh token (idempotent)
GET  /api/auth/me         return the current user (requires Bearer token)
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.extensions import csrf, limiter
from backend.services.auth_service import AuthError, AuthService
from backend.utils.auth import api_require_auth, get_current_user
from backend.schemas import LoginSchema, LogoutSchema, RefreshSchema, RegisterSchema, load_json

api_auth_bp = Blueprint("api_auth", __name__, url_prefix="/api/auth")

# Exempt the entire API auth blueprint from CSRF checks.
# JWT Bearer tokens provide equivalent request-forgery protection for API clients.
csrf.exempt(api_auth_bp)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _user_dict(user) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role.value,
        "reputation_score": user.reputation_score,
    }


def _token_response(user, access_token: str, refresh_token: str, status: int = 200):
    return (
        jsonify(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "user": _user_dict(user),
            }
        ),
        status,
    )


# ── Register ──────────────────────────────────────────────────────────────────


@api_auth_bp.post("/register")
@limiter.limit("5 per hour")
def register():
    """Create a new account and return a token pair.

    Request body (JSON)
    -------------------
    email        str  required
    username     str  required
    password     str  required (min 15 chars)
    display_name str  optional
    """
    data, err = load_json(RegisterSchema())
    if err:
        return err
    email = data["email"].strip()
    username = data["username"].strip()
    password = data["password"]
    display_name = (data["display_name"] or "").strip() or None

    try:
        user = AuthService.register(email, username, password, display_name)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    access_token, refresh_token = AuthService.issue_tokens(user)
    return _token_response(user, access_token, refresh_token, status=201)


# ── Login ─────────────────────────────────────────────────────────────────────


@api_auth_bp.post("/login")
@limiter.limit("10 per minute")
def login():
    """Verify credentials and return a token pair.

    Request body (JSON)
    -------------------
    email    str  required
    password str  required
    """
    data, err = load_json(LoginSchema())
    if err:
        return err
    email = data["email"].strip()
    password = data["password"]

    try:
        user, access_token, refresh_token = AuthService.login(email, password)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    return _token_response(user, access_token, refresh_token)


# ── Refresh ───────────────────────────────────────────────────────────────────


@api_auth_bp.post("/refresh")
@limiter.limit("5 per minute")
def refresh():
    """Rotate the refresh token and return a new token pair.

    Implements single-use refresh tokens: submitting the same token twice
    returns 401 on the second call.

    Request body (JSON)
    -------------------
    refresh_token  str  required
    """
    data, err = load_json(RefreshSchema())
    if err:
        return err
    token = data["refresh_token"].strip()

    try:
        access_token, refresh_token = AuthService.rotate_refresh_token(token)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
        }
    )


# ── Logout ────────────────────────────────────────────────────────────────────


@api_auth_bp.post("/logout")
def logout():
    """Revoke a refresh token (idempotent).

    The client should also discard the access token locally; it remains
    valid until expiry but is short-lived (15 min) and has no server state.

    Request body (JSON)
    -------------------
    refresh_token  str  required
    """
    data, err = load_json(LogoutSchema())
    if err:
        return err
    token = data["refresh_token"].strip()

    try:
        payload = AuthService.verify_refresh_token(token)
        AuthService.revoke_refresh_token(payload["jti"])
    except AuthError:
        pass  # Already revoked / expired — idempotent

    return jsonify({"message": "Logged out."})


# ── Current user ──────────────────────────────────────────────────────────────


@api_auth_bp.get("/me")
@api_require_auth
def me():
    """Return the authenticated user's profile.

    Requires ``Authorization: Bearer <access_token>``.
    """
    user = get_current_user()
    return jsonify(_user_dict(user))
