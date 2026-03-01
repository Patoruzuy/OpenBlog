"""SSR — workspace invitation redemption routes.

URL structure
-------------
GET /invites/<raw_token>   Redeem an invitation link

Security
--------
* No workspace existence is revealed for invalid/expired/revoked tokens.
  The response is a generic "invitation invalid" page regardless of the
  failure reason.
* All invite pages are marked ``noindex, nofollow`` to prevent web crawlers
  from indexing (and therefore leaking) invite URLs that appear in browser
  history or referrer logs.
* Cache-Control: private, no-store on all responses (never cached by proxy).
* Unauthenticated visitors are redirected to login with ``?next=<invite_url>``
  so they return to the invite after signing in.

Non-leakage contract
--------------------
:func:`~backend.services.invite_service.validate_invite` returns only a
status code string — no workspace name or slug.  The templates in this
blueprint MUST NOT include workspace details when the token is invalid.
"""
from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    url_for,
)

from backend.extensions import db
from backend.services import invite_service
from backend.utils.auth import get_current_user

invite_bp = Blueprint("invites", __name__, url_prefix="/invites")


# ── Blueprint-wide Cache-Control ──────────────────────────────────────────────


@invite_bp.after_request
def _no_store(response):
    """Enforce private, no-store on every invite response."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers.pop("Expires", None)
    response.headers.pop("Pragma", None)
    return response


# ── Invite redemption ─────────────────────────────────────────────────────────


@invite_bp.get("/<raw_token>")
def redeem(raw_token: str):
    """Display the invitation page and redeem it if the user is authenticated.

    Flow
    ----
    1. Unauthenticated → redirect to ``/auth/login?next=/invites/<token>``
    2. Authenticated + status != valid → render generic invalid page
       (no workspace details exposed).
    3. Authenticated + status valid → redeem, commit, redirect to workspace
       dashboard.

    Template variables (both success and failure paths share the same base):
    - ``status`` — one of ``"valid"``, ``"expired"``, ``"revoked"``,
      ``"used_up"``, ``"not_found"``
    """
    user = get_current_user()

    if user is None:
        # Redirect to login, preserving the invite URL as `next`.
        next_url = url_for("invites.redeem", raw_token=raw_token)
        return redirect(url_for("auth.login", next=next_url))

    status = invite_service.validate_invite(raw_token)

    if status != "valid":
        # Do NOT include workspace name/slug in the rendered context.
        return render_template("invites/invalid.html", status=status), 200

    try:
        member = invite_service.redeem_invite(raw_token, user)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        # status string is the exception message (not_found / revoked / …)
        return render_template("invites/invalid.html", status=str(exc)), 200
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return render_template("invites/invalid.html", status="error"), 500

    # Load the workspace to redirect — safe because the user is now a member.
    from sqlalchemy import select  # noqa: PLC0415

    from backend.models.workspace import Workspace  # noqa: PLC0415

    workspace = db.session.get(Workspace, member.workspace_id)
    flash("You have successfully joined the workspace.", "success")
    return redirect(
        url_for("workspace.dashboard", workspace_slug=workspace.slug)
    )
