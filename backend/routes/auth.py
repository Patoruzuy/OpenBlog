"""SSR authentication routes.

Handles cookie-session–based login/register/logout for the Jinja2 front-end.
On successful login, ``session["user_id"]`` is set and the browser is redirected.

Routes
------
GET  /auth/login               render login form
POST /auth/login               process login, set session cookie, redirect
GET  /auth/register            render registration form
POST /auth/register            create user, set session cookie, redirect
GET  /auth/logout              clear session, redirect to home
GET  /auth/forgot-password     render forgot-password form
POST /auth/forgot-password     send reset email (always succeeds visibly)
GET  /auth/reset-password/<t>  render reset-password form
POST /auth/reset-password/<t>  set new password, redirect to login
GET  /auth/verify/<t>          verify email address from link
GET  /auth/resend-verification  trigger a new verification email
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from backend.extensions import limiter
from backend.services.auth_service import AuthError, AuthService

ssr_auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@ssr_auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute")
def login():
    """Render the login form (GET) or process credentials (POST)."""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        try:
            user, _at, _rt = AuthService.login(email, password)
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            next_url = request.form.get("next") or url_for("index.index")
            return redirect(next_url)
        except AuthError as exc:
            flash(str(exc), "error")

    return render_template("auth/login.html")


@ssr_auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def register():
    """Render the register form (GET) or create an account (POST)."""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        username = (request.form.get("username") or "").strip()
        display_name = (request.form.get("display_name") or "").strip() or None
        password = request.form.get("password") or ""
        try:
            user = AuthService.register(
                email, username, password, display_name=display_name
            )
            session.clear()
            session["user_id"] = user.id
            session.permanent = True

            # Send email verification in background.
            try:
                from backend.tasks.email import send_verification_email  # noqa: PLC0415

                token = AuthService.generate_email_verification_token(user)
                send_verification_email.delay(user.email, token)
            except Exception as exc:
                current_app.logger.warning("Failed to send verification email: %s", exc)
                pass  # Verification email is best-effort; don't block registration.

            next_url = request.form.get("next") or url_for("index.index")
            return redirect(next_url)
        except AuthError as exc:
            flash(str(exc), "error")

    return render_template("auth/register.html")


@ssr_auth_bp.get("/logout")
def logout():
    """Clear the session and redirect to the home page."""
    session.clear()
    return redirect(url_for("index.index"))


# ── Password reset ─────────────────────────────────────────────────────────────


@ssr_auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    """Request a password-reset email."""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        # Silently succeed even if email not found to prevent enumeration.
        from sqlalchemy import select  # noqa: PLC0415

        from backend.extensions import db  # noqa: PLC0415
        from backend.models.user import User  # noqa: PLC0415

        user = db.session.scalar(select(User).where(User.email == email))
        if user and user.password_hash:
            try:
                from backend.tasks.email import (
                    send_password_reset_email,  # noqa: PLC0415
                )

                token = AuthService.generate_password_reset_token(user)
                send_password_reset_email.delay(user.email, token)
            except Exception as exc:
                current_app.logger.warning(
                    "Failed to send password reset email: %s", exc
                )
        flash(
            "If that email exists in our system, a password reset link has been sent.",
            "info",
        )
        return redirect(url_for("auth.forgot_password"))

    return render_template("auth/forgot_password.html")


@ssr_auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    """Validate the reset token and allow the user to set a new password."""
    try:
        user = AuthService.confirm_password_reset_token(token)
    except AuthError:
        return render_template("auth/token_invalid.html", mode="reset")

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html", token=token)
        try:
            AuthService.set_new_password(user, password)
            flash("Password updated successfully. Please sign in.", "success")
            return redirect(url_for("auth.login"))
        except AuthError as exc:
            flash(str(exc), "error")

    return render_template("auth/reset_password.html", token=token)


# ── Email verification ─────────────────────────────────────────────────────────


@ssr_auth_bp.get("/verify/<token>")
def verify_email(token: str):
    """Verify the user's email address via link in the verification email."""
    try:
        AuthService.confirm_email_verification_token(token)
        return render_template("auth/email_verified.html")
    except AuthError:
        return render_template("auth/token_invalid.html", mode="verify")


@ssr_auth_bp.get("/resend-verification")
@limiter.limit("3 per hour")
def resend_verification():
    """Resend the verification email for the currently logged-in user."""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.user import User  # noqa: PLC0415

    user = db.session.get(User, user_id)
    if user and not user.is_email_verified:
        try:
            from backend.tasks.email import send_verification_email  # noqa: PLC0415

            token = AuthService.generate_email_verification_token(user)
            send_verification_email.delay(user.email, token)
        except Exception as exc:
            current_app.logger.warning("Failed to resend verification email: %s", exc)
        flash("Verification email sent — check your inbox.", "info")
    elif user and user.is_email_verified:
        flash("Your email is already verified.", "success")

    return redirect(request.referrer or url_for("index.index"))
