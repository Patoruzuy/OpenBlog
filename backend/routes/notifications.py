"""SSR — Notifications inbox.

Routes
------
GET /notifications/    current user's notification inbox, paginated
                       ?unread_only=1   filter to unread notifications only
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from backend.services.notification_service import NotificationService
from backend.utils.auth import get_current_user, require_auth

ssr_notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")

_PER_PAGE = 20


@ssr_notifications_bp.get("/")
@require_auth
def notification_inbox():
    """Render the current user's notification inbox."""
    user = get_current_user()  # guaranteed non-None by @require_auth
    page = max(1, request.args.get("page", 1, type=int))
    unread_only = request.args.get("unread_only", "0") in {"1", "true", "yes"}

    notifications, total = NotificationService.list_for_user(
        user.id,
        unread_only=unread_only,
        page=page,
        per_page=_PER_PAGE,
    )
    pages = (total + _PER_PAGE - 1) // _PER_PAGE if total else 0
    unread_count = NotificationService.unread_count(user.id)

    return render_template(
        "notifications/index.html",
        notifications=notifications,
        page=page,
        pages=pages,
        total=total,
        unread_count=unread_count,
        unread_only=unread_only,
    )
