"""SSR — Notifications inbox.

Routes
------
GET  /notifications/                current user's inbox, paginated
                                    ?grouped=1       (default) grouped by target
                                    ?target_type=X&target_id=Y  filtered flat list
POST /notifications/<id>/read       mark a single notification as read
POST /notifications/read-all        mark all notifications as read
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from backend.services.notification_service import NotificationService
from backend.utils.auth import get_current_user, require_auth

ssr_notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")

_PER_PAGE = 20


@ssr_notifications_bp.after_request
def _no_store(response):
    """Never cache the notifications inbox."""
    response.headers["Cache-Control"] = "private, no-store"
    return response


@ssr_notifications_bp.get("/")
@require_auth
def notification_inbox():
    """Render the current user's notification inbox.

    When ``target_type`` + ``target_id`` are provided as query params, a flat
    paginated list for that specific target is shown (the "expanded group" view).
    Otherwise the default grouped view is rendered — one row per (target_type,
    target_id) pair, with a count badge on groups that have multiple notifications.
    """
    user = get_current_user()
    page = max(1, request.args.get("page", 1, type=int))
    unread_only = request.args.get("unread_only", "0") in {"1", "true", "yes"}
    target_type = request.args.get("target_type")
    target_id = request.args.get("target_id", type=int)

    unread_count = NotificationService.unread_count(user.id)

    # ── Filtered flat view (expanded group) ─────────────────────────────────
    if target_type and target_id is not None:
        notifications, total = NotificationService.list_for_user(
            user.id,
            unread_only=unread_only,
            page=page,
            per_page=_PER_PAGE,
            target_type=target_type,
            target_id=target_id,
        )
        pages = (total + _PER_PAGE - 1) // _PER_PAGE if total else 0
        return render_template(
            "notifications/index.html",
            notifications=notifications,
            grouped=False,
            groups=None,
            page=page,
            pages=pages,
            total=total,
            unread_count=unread_count,
            unread_only=unread_only,
            filter_target_type=target_type,
            filter_target_id=target_id,
        )

    # ── Grouped view (default inbox) ─────────────────────────────────────────
    groups = NotificationService.list_grouped_for_user(
        user.id,
        unread_only=unread_only,
    )
    return render_template(
        "notifications/index.html",
        notifications=None,
        grouped=True,
        groups=groups,
        page=1,
        pages=1,
        total=len(groups),
        unread_count=unread_count,
        unread_only=unread_only,
        filter_target_type=None,
        filter_target_id=None,
    )


@ssr_notifications_bp.post("/<int:notification_id>/read")
@require_auth
def mark_notification_read(notification_id: int):
    """Mark a single notification as read, then redirect back to inbox."""
    user = get_current_user()
    try:
        NotificationService.mark_read(notification_id, user.id)
    except Exception:
        pass  # Silently ignore not-found; page refreshes cleanly.
    return redirect(request.referrer or url_for("notifications.notification_inbox"))


@ssr_notifications_bp.post("/read-all")
@require_auth
def mark_all_notifications_read():
    """Mark all notifications as read, then redirect back to inbox."""
    user = get_current_user()
    NotificationService.mark_all_read(user.id)
    return redirect(url_for("notifications.notification_inbox"))
