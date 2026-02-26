"""JSON API — badge definitions and user badge management.

Routes
------
GET   /api/badges/                       list all badge definitions     [public]
GET   /api/users/<username>/badges       list badges earned by a user   [public]
POST  /api/users/<username>/badges       award a badge to a user        [admin]
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.extensions import csrf
from backend.services.badge_service import BadgeError, BadgeService
from backend.services.user_service import UserService
from backend.utils.auth import api_require_role

api_badges_bp = Blueprint("api_badges", __name__, url_prefix="/api")
csrf.exempt(api_badges_bp)


# ── Serialisers ───────────────────────────────────────────────────────────────


def _badge_dict(badge) -> dict:
    return {
        "key": badge.key,
        "name": badge.name,
        "description": badge.description,
        "icon_url": badge.icon_url,
    }


def _user_badge_dict(user_badge) -> dict:
    return {
        "badge": _badge_dict(user_badge.badge),
        "awarded_at": user_badge.awarded_at.isoformat(),
    }


# ── Badge definitions ─────────────────────────────────────────────────────────


@api_badges_bp.get("/badges/")
def list_badge_definitions():
    """Return all badge definitions (public)."""
    badges = BadgeService.list_all_definitions()
    return jsonify([_badge_dict(b) for b in badges])


# ── User badges ───────────────────────────────────────────────────────────────


@api_badges_bp.get("/users/<username>/badges")
def list_user_badges(username: str):
    """Return all badges earned by *username* (public)."""
    user = UserService.get_by_username(username)
    if user is None:
        return jsonify({"error": "User not found."}), 404

    user_badges = BadgeService.list_for_user(user.id)
    return jsonify([_user_badge_dict(ub) for ub in user_badges])


@api_badges_bp.post("/users/<username>/badges")
@api_require_role("admin")
def award_badge(username: str):
    """Award a badge to *username* (admin only).

    Request body
    ------------
    ``badge_key``  — key of the badge to award (required)
    """
    user = UserService.get_by_username(username)
    if user is None:
        return jsonify({"error": "User not found."}), 404

    body = request.get_json(silent=True) or {}
    badge_key = body.get("badge_key", "").strip()
    if not badge_key:
        return jsonify({"error": "badge_key is required."}), 400

    try:
        user_badge = BadgeService.award(user.id, badge_key)
    except BadgeError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    if user_badge is None:
        # Already awarded — return 200 with the existing record.
        existing = [
            ub
            for ub in BadgeService.list_for_user(user.id)
            if ub.badge.key == badge_key
        ]
        if existing:
            return jsonify(_user_badge_dict(existing[0]))
        return jsonify({"error": "Badge not found on user."}), 404

    return jsonify(_user_badge_dict(user_badge)), 201
