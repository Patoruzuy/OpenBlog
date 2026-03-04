"""SSR blueprint — public reputation ledger page.

Routes
------
GET /users/<username>/reputation   public reputation event history

Security notes
--------------
- Only ``workspace_id IS NULL`` events are ever passed to the template.
  The SQL filter is enforced inside ``ReputationService.list_public_events``
  and cannot be overridden by query parameters.
- Cache-Control is ``public, max-age=60`` because the response contains no
  workspace-scoped data whatsoever.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, make_response, render_template

from backend.services.privacy_service import PrivacyService
from backend.services.reputation_service import ReputationService
from backend.services.user_service import UserService
from backend.utils.auth import get_current_user

ssr_reputation_bp = Blueprint("reputation", __name__, url_prefix="/users")


@ssr_reputation_bp.get("/<username>/reputation")
def reputation_page(username: str) -> Response:
    """Render the public reputation page for *username*.

    Filters
    -------
    - Only active users are accessible (inactive → 404).
    - If the user has set their profile private the page returns 404 so no
      metadata leaks.
    - Only public events (``workspace_id IS NULL``) are shown, regardless of
      who is viewing.
    """
    user = UserService.get_by_username(username)
    if user is None or not user.is_active:
        abort(404)

    viewer = get_current_user()
    privacy_view = PrivacyService.get_public_view(user, viewer)
    if not privacy_view.get("visible", True):
        abort(404)

    # SQL-level enforcement: workspace_id IS NULL is hardcoded in the service.
    events = ReputationService.list_public_events(user.id, limit=50)
    total = ReputationService.get_public_total(user.id)

    resp = make_response(
        render_template(
            "users/reputation.html",
            profile_user=user,
            events=events,
            public_total=total,
            viewer=viewer,
        )
    )
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp
