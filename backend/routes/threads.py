"""Thread routes — one-click email unsubscribe.

Routes
------
GET /threads/<slug>/unsubscribe?token=  verify HMAC token, remove subscription,
                                        render confirmation page

The token is produced by ``NotificationDeliveryService.make_unsubscribe_token``
and encodes ``(user_id, post_id)`` signed with the application's SECRET_KEY.
No database state is needed for revocation; the token is permanent unless the
user clicks "Follow" again.
"""

from __future__ import annotations

import logging

from flask import Blueprint, render_template, request

from backend.services.notification_delivery_service import NotificationDeliveryService
from backend.services.thread_subscription_service import ThreadSubscriptionService

log = logging.getLogger(__name__)

threads_bp = Blueprint("threads", __name__, url_prefix="/threads")


@threads_bp.get("/<slug>/unsubscribe")
def unsubscribe(slug: str):  # type: ignore[return]
    """One-click unsubscribe from thread email notifications.

    Verifies the HMAC token from the query string, removes the thread
    subscription, and renders a confirmation page.  Shows an error page
    for missing, tampered, or structurally invalid tokens instead of
    exposing any technical details.
    """
    token = request.args.get("token", "")
    if not token:
        log.debug("threads.unsubscribe called without token for slug=%s", slug)
        return render_template("threads/error.html", slug=slug), 400

    result = NotificationDeliveryService.verify_unsubscribe_token(token)
    if result is None:
        log.debug("threads.unsubscribe bad token for slug=%s", slug)
        return render_template("threads/error.html", slug=slug), 400

    user_id, _post_id = result

    # Remove the subscription (idempotent — OK if already removed)
    try:
        ThreadSubscriptionService.unsubscribe(user_id=user_id, post_id=_post_id)
    except Exception:
        log.exception(
            "Error unsubscribing user_id=%s from post_id=%s", user_id, _post_id
        )
        return render_template("threads/error.html", slug=slug), 500

    return render_template("threads/unsubscribed.html", slug=slug)
