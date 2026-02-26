"""Authentication utilities and view decorators.

Two families of decorators are provided:

SSR decorators — redirect to /auth/login on failure
  @require_auth              checks session["user_id"] then Bearer JWT
  @require_role("admin")     additionally enforces a role

API decorators — return JSON 401/403 on failure
  @api_require_auth          checks Bearer JWT only
  @api_require_role("admin") additionally enforces a role

Current-user helpers
  get_current_user()  returns the authenticated User or None (no error)
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from flask import g, jsonify, redirect, request, session, url_for

from backend.extensions import db
from backend.models.user import User, UserRole
from backend.services.auth_service import AuthError, AuthService


def _load_user_from_request() -> User | None:
    """Try Flask session first, then Bearer JWT.  Returns None on any failure."""
    # 1. Flask session — set by the SSR login form.
    user_id = session.get("user_id")
    if user_id:
        return db.session.get(User, int(user_id))

    # 2. Authorization: Bearer <access_token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = AuthService.verify_access_token(token)
            return db.session.get(User, int(payload["sub"]))
        except AuthError:
            return None

    return None


_UNSET = object()  # sentinel: g._current_user not yet resolved this request


def get_current_user() -> User | None:
    """Return the authenticated user for this request (cached in ``g``)."""
    if getattr(g, "_current_user", _UNSET) is _UNSET:
        g._current_user = _load_user_from_request()
    return g._current_user  # type: ignore[return-value]


# ── SSR decorators (redirect on failure) ──────────────────────────────────────


def require_auth(fn: Callable) -> Callable:
    """Redirect to /auth/login if the request carries no valid credentials."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        user = get_current_user()
        if user is None or not user.is_active:
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)

    return wrapper


def require_role(*roles: str | UserRole) -> Callable:
    """Require the current user to have one of *roles*.

    Usage::

        @require_role("admin", "editor")
        def admin_view(): ...
    """
    role_values = {r.value if isinstance(r, UserRole) else r for r in roles}

    def decorator(fn: Callable) -> Callable:
        @require_auth
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            user = get_current_user()
            if user is None or user.role.value not in role_values:
                return redirect(url_for("auth.login"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ── API decorators (JSON response on failure) ─────────────────────────────────


def api_require_auth(fn: Callable) -> Callable:
    """Return JSON 401 if the request carries no valid Bearer token."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header."}), 401
        token = auth_header[7:]
        try:
            payload = AuthService.verify_access_token(token)
        except AuthError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        user = db.session.get(User, int(payload["sub"]))
        if user is None or not user.is_active:
            return jsonify({"error": "User not found or deactivated."}), 401
        g._current_user = user
        return fn(*args, **kwargs)

    return wrapper


def api_require_role(*roles: str | UserRole) -> Callable:
    """Return JSON 403 if the current user lacks the required role."""
    role_values = {r.value if isinstance(r, UserRole) else r for r in roles}

    def decorator(fn: Callable) -> Callable:
        @api_require_auth
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            user = get_current_user()
            if user is None or user.role.value not in role_values:
                return jsonify({"error": "Insufficient permissions."}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
