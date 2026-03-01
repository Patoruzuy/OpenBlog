"""Newsletter routes — subscribe, confirm, unsubscribe.

Routes
------
POST /newsletter/subscribe       accept email, always return same success msg
GET  /newsletter/confirm?token=  activate subscription
GET  /newsletter/unsubscribe?token= opt out

All responses are enumeration-safe: the subscribe endpoint returns the
same message regardless of whether the email is known.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from backend.extensions import db, limiter
from backend.services.newsletter_service import NewsletterError, NewsletterService
from backend.utils.auth import get_current_user

newsletter_bp = Blueprint("newsletter", __name__, url_prefix="/newsletter")

_SUCCESS_MSG = (
    "If that email is not already subscribed, a confirmation link has been sent. "
    "Check your inbox!"
)


@newsletter_bp.post("/subscribe")
@limiter.limit("5 per hour")
def subscribe():
    """Accept a newsletter subscription request.

    Always shows the same success message regardless of email status to
    prevent enumeration.
    """
    email = (request.form.get("email") or "").strip().lower()
    locale = request.form.get("locale") or current_app.config.get(
        "BABEL_DEFAULT_LOCALE", "en"
    )
    source = request.form.get("source") or "footer_form"

    # Determine user_id if the submitter is logged in.
    user = get_current_user()
    user_id = user.id if user else None

    if email and "@" in email:
        try:
            sub, confirm_token = NewsletterService.subscribe(
                email, source=source, locale=locale, user_id=user_id
            )
            db.session.commit()

            # Fire confirm email only for pending subscriptions.
            if sub.status == "pending":
                try:
                    from backend.tasks.email import (
                        send_newsletter_confirm_email,  # noqa: PLC0415
                    )

                    send_newsletter_confirm_email.delay(email, confirm_token, locale)
                except Exception as exc:
                    current_app.logger.warning(
                        "Failed to queue newsletter confirm email: %s", exc
                    )
        except NewsletterError as exc:
            current_app.logger.warning("Newsletter subscribe error: %s", exc)
        except Exception as exc:
            current_app.logger.error("Newsletter subscribe unexpected error: %s", exc)
            db.session.rollback()

    # Always show the same response (enumeration-safe).
    flash(_SUCCESS_MSG, "info")
    # Return to the page that submitted the form (next param or referrer).
    next_url = request.form.get("next") or request.referrer or url_for("index.index")
    return redirect(next_url)


@newsletter_bp.get("/confirm")
def confirm():
    """Activate a newsletter subscription via the token in the confirmation email."""
    token = request.args.get("token", "")
    if not token:
        return render_template("newsletter/error.html", reason="missing_token"), 400

    try:
        sub = NewsletterService.confirm(token)
        db.session.commit()
        # Link subscription to user account if email matches.
        user = get_current_user()
        if user and sub.email == user.email.lower():
            NewsletterService.link_to_user(sub.email, user.id)
            db.session.commit()
        return render_template("newsletter/confirmed.html", email=sub.email)
    except NewsletterError as exc:
        return render_template("newsletter/error.html", reason=str(exc)), 400


@newsletter_bp.get("/unsubscribe")
def unsubscribe():
    """One-click unsubscribe via the token in any newsletter email."""
    token = request.args.get("token", "")
    if not token:
        return render_template("newsletter/error.html", reason="missing_token"), 400

    try:
        sub = NewsletterService.unsubscribe(token)
        db.session.commit()
        return render_template("newsletter/unsubscribed.html", email=sub.email)
    except NewsletterError as exc:
        return render_template("newsletter/error.html", reason=str(exc)), 400
