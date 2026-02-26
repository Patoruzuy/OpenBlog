"""JSON API — notification endpoints.

Routes
------
GET   /api/notifications/             list notifications (paginated)  [authenticated]
GET   /api/notifications/unread-count unread count                    [authenticated]
POST  /api/notifications/<id>/read    mark a single notification read [authenticated]
POST  /api/notifications/read-all     mark all notifications read     [authenticated]
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.extensions import csrf
from backend.services.notification_service import NotificationError, NotificationService
from backend.utils.auth import api_require_auth, get_current_user

api_notifications_bp = Blueprint(
    "api_notifications", __name__, url_prefix="/api/notifications"
)
csrf.exempt(api_notifications_bp)


# ── Serialiser ────────────────────────────────────────────────────────────────


def _notif_dict(n) -> dict:
    return {
        "id": n.id,
        "type": n.notification_type,
        "title": n.title,
        "body": n.body,
        "payload": n.payload,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


# ── GET /api/notifications/ ───────────────────────────────────────────────────


@api_notifications_bp.get("/")
@api_require_auth
def list_notifications():
    """Return paginated notifications for the current user."""
    user = get_current_user()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))
    unread_only = request.args.get("unread_only", "false").lower() == "true"

    notifications, total = NotificationService.list_for_user(
        user.id, unread_only=unread_only, page=page, per_page=per_page
    )
    return jsonify(
        {
            "notifications": [_notif_dict(n) for n in notifications],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if total else 0,
            "unread_count": NotificationService.unread_count(user.id),
        }
    )


# ── GET /api/notifications/unread-count ───────────────────────────────────────


@api_notifications_bp.get("/unread-count")
@api_require_auth
def unread_count():
    """Return the number of unread notifications for the current user."""
    user = get_current_user()
    return jsonify({"unread_count": NotificationService.unread_count(user.id)})


# ── POST /api/notifications/<id>/read ────────────────────────────────────────


@api_notifications_bp.post("/<int:notification_id>/read")
@api_require_auth
def mark_read(notification_id: int):
    """Mark a single notification as read."""
    user = get_current_user()
    try:
        notif = NotificationService.mark_read(notification_id, user.id)
    except NotificationError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(_notif_dict(notif))


# ── POST /api/notifications/read-all ─────────────────────────────────────────


@api_notifications_bp.post("/read-all")
@api_require_auth
def mark_all_read():
    """Mark all unread notifications as read."""
    user = get_current_user()
    count = NotificationService.mark_all_read(user.id)
    return jsonify(
        {
            "marked_read": count,
            "unread_count": 0,
        }
    )
