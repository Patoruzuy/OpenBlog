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

from flask import Blueprint, jsonify, request

from backend.extensions import csrf, limiter
from backend.services.auth_service import AuthError, AuthService
from backend.utils.auth import api_require_auth, get_current_user

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
    password     str  required (min 8 chars)
    display_name str  optional
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip() or None

    if not email or not username or not password:
        return jsonify({"error": "email, username, and password are required."}), 400

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
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email and password are required."}), 400

    try:
        user, access_token, refresh_token = AuthService.login(email, password)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    return _token_response(user, access_token, refresh_token)


# ── Refresh ───────────────────────────────────────────────────────────────────


@api_auth_bp.post("/refresh")
def refresh():
    """Rotate the refresh token and return a new token pair.

    Implements single-use refresh tokens: submitting the same token twice
    returns 401 on the second call.

    Request body (JSON)
    -------------------
    refresh_token  str  required
    """
    data = request.get_json(silent=True) or {}
    token = (data.get("refresh_token") or "").strip()
    if not token:
        return jsonify({"error": "refresh_token is required."}), 400

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
    data = request.get_json(silent=True) or {}
    token = (data.get("refresh_token") or "").strip()
    if not token:
        return jsonify({"error": "refresh_token is required."}), 400

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
