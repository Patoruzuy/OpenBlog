"""SSR authentication routes.

Handles cookie-session–based login/register/logout for the Jinja2 front-end.
On successful login, ``session["user_id"]`` is set and the browser is redirected.

Routes
------
GET  /auth/login     render login form
POST /auth/login     process login, set session cookie, redirect
GET  /auth/register  render registration form
POST /auth/register  create user, set session cookie, redirect
GET  /auth/logout    clear session, redirect to home
"""

from __future__ import annotations

from flask import (
    Blueprint,
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
        password = request.form.get("password") or ""
        try:
            user = AuthService.register(email, username, password)
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            return redirect(url_for("index.index"))
        except AuthError as exc:
            flash(str(exc), "error")

    return render_template("auth/register.html")


@ssr_auth_bp.get("/logout")
def logout():
    """Clear the session and redirect to the home page."""
    session.clear()
    return redirect(url_for("index.index"))
