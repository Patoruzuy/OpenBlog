"""Admin authentication and permission helpers.

Three decorators:

  @require_admin_access    — admits admin AND editor AND moderator
  @require_admin           — admits admin only
  @admin_permission(cap)   — admits roles that have a named capability

Capability map
--------------
  manage_content   → admin, editor
  manage_users     → admin
  moderate         → admin, editor
  view_analytics   → admin, editor
  manage_settings  → admin
  view_audit       → admin

All checks happen server-side in the decorator; templates never decide access.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from flask import redirect, request, url_for

from backend.models.user import User, UserRole
from backend.utils.auth import get_current_user

# ── Capability table ──────────────────────────────────────────────────────────

_CAPABILITIES: dict[str, frozenset[str]] = {
    "manage_content": frozenset({"admin", "editor"}),
    "manage_users": frozenset({"admin"}),
    "moderate": frozenset({"admin", "editor"}),
    "view_analytics": frozenset({"admin", "editor"}),
    "manage_settings": frozenset({"admin"}),
    "view_audit": frozenset({"admin"}),
}

#: Roles that may enter the admin area at all.
_ADMIN_ROLES: frozenset[str] = frozenset({"admin", "editor"})


def _check_admin_user() -> User | None:
    """Return user if they may access admin, else None."""
    user = get_current_user()
    if user is None or not user.is_active:
        return None
    if user.role.value not in _ADMIN_ROLES:
        return None
    return user


def require_admin_access(fn: Callable) -> Callable:
    """Redirect to login (or 403) if the user cannot access the admin area."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        user = _check_admin_user()
        if user is None:
            return redirect(url_for("auth.login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn: Callable) -> Callable:
    """Require the *admin* role precisely (super-admin only)."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        user = get_current_user()
        if user is None or not user.is_active:
            return redirect(url_for("auth.login", next=request.path))
        if user.role != UserRole.admin:
            from flask import render_template  # noqa: PLC0415

            return render_template("admin/403.html"), 403
        return fn(*args, **kwargs)

    return wrapper


def require_capability(capability: str) -> Callable:
    """Decorator factory: require the current user to have *capability*.

    Usage::

        @require_capability("manage_users")
        def admin_users(): ...
    """
    allowed = _CAPABILITIES.get(capability, frozenset())

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            user = get_current_user()
            if user is None or not user.is_active:
                return redirect(url_for("auth.login", next=request.path))
            if user.role.value not in allowed:
                from flask import render_template  # noqa: PLC0415

                return render_template("admin/403.html"), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def current_admin_user() -> User:
    """Return the current admin user; assumes @require_admin_access already ran."""
    user = get_current_user()
    assert user is not None
    return user


def can(capability: str) -> bool:
    """Template helper: true if the current admin user has *capability*."""
    user = get_current_user()
    if user is None:
        return False
    allowed = _CAPABILITIES.get(capability, frozenset())
    return user.role.value in allowed
