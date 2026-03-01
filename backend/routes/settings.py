"""Settings blueprint — User Portal settings screens.

Routes
------
GET  /settings/                 → redirect to /settings/profile
GET  /settings/profile          → profile form
POST /settings/profile          → save profile + optional avatar upload
GET  /settings/privacy          → privacy/identity form
POST /settings/privacy          → save privacy settings
GET  /settings/security         → change password, active sessions
POST /settings/security/password→ change password
GET  /settings/accounts         → connected accounts (GitHub, etc.)
POST /settings/accounts/connect → add a connected account (manual GitHub URL)
POST /settings/accounts/disconnect → remove a connected account
GET  /settings/repositories     → repository list
POST /settings/repositories/add → add a repository
POST /settings/repositories/<id>/edit   → edit a repository
POST /settings/repositories/<id>/delete → delete a repository
POST /settings/repositories/reorder    → reorder (JSON body)
GET  /settings/contributions    → contribution history with identity labels

All routes require an authenticated, active user (``@require_auth``).
"""

from __future__ import annotations

import os

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import desc, select

from backend.extensions import db
from backend.models.portal import UserConnectedAccount
from backend.models.revision import Revision
from backend.models.user import User
from backend.services.auth_service import AuthError, AuthService
from backend.services.contribution_identity_service import ContributionIdentityService
from backend.services.privacy_service import PrivacyService
from backend.services.profile_service import ProfileService, ProfileServiceError
from backend.services.repository_service import (
    RepositoryService,
    RepositoryServiceError,
)
from backend.utils.auth import get_current_user, require_auth
from backend.utils.validation import validate_url

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_user_or_abort() -> User:
    """Return the current user; caller has already applied @require_auth."""
    user = get_current_user()
    assert user is not None  # guaranteed by @require_auth
    return user


def _avatar_upload_folder() -> str:
    """Compute the absolute path to the avatar upload directory."""
    return os.path.join(
        current_app.root_path,
        "static",
        "uploads",
        "avatars",
    )


# ---------------------------------------------------------------------------
# Index redirect
# ---------------------------------------------------------------------------


@settings_bp.route("/")
@require_auth
def index():
    return redirect(url_for("settings.profile"))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@settings_bp.route("/profile", methods=["GET", "POST"])
@require_auth
def profile():
    user = _current_user_or_abort()
    privacy = PrivacyService.get_or_create_privacy(user)

    if request.method == "POST":
        try:
            ProfileService.update_profile(
                user,
                display_name=request.form.get("display_name"),
                headline=request.form.get("headline"),
                bio=request.form.get("bio"),
                location=request.form.get("location"),
                website_url=request.form.get("website_url"),
                github_url=request.form.get("github_url"),
                tech_stack=request.form.get("tech_stack"),
            )
            # Handle optional avatar upload
            avatar_file = request.files.get("avatar")
            if avatar_file and avatar_file.filename:
                ProfileService.save_avatar(
                    user,
                    avatar_file.stream,
                    avatar_file.mimetype,
                    _avatar_upload_folder(),
                )
            flash("Profile updated.", "success")
        except ProfileServiceError as exc:
            flash(exc.message, "error")
        return redirect(url_for("settings.profile"))

    return render_template(
        "settings/profile.html",
        profile_user=user,
        privacy=privacy,
        active_section="profile",
    )


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


@settings_bp.route("/privacy", methods=["GET", "POST"])
@require_auth
def privacy():
    user = _current_user_or_abort()
    privacy = PrivacyService.get_or_create_privacy(user)

    if request.method == "POST":
        try:
            ProfileService.update_privacy(
                user,
                profile_visibility=request.form.get("profile_visibility"),
                default_identity_mode=request.form.get("default_identity_mode"),
                pseudonymous_alias=request.form.get("pseudonymous_alias"),
                show_avatar="show_avatar" in request.form,
                show_bio="show_bio" in request.form,
                show_location="show_location" in request.form,
                show_social_links="show_social_links" in request.form,
                show_repositories="show_repositories" in request.form,
                show_contributions="show_contributions" in request.form,
                searchable_profile="searchable_profile" in request.form,
            )
            flash("Privacy settings saved.", "success")
        except ProfileServiceError as exc:
            flash(exc.message, "error")
        return redirect(url_for("settings.privacy"))

    return render_template(
        "settings/privacy.html",
        privacy=privacy,
        active_section="privacy",
    )


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


@settings_bp.route("/security", methods=["GET"])
@require_auth
def security():
    user = _current_user_or_abort()
    return render_template(
        "settings/security.html",
        profile_user=user,
        active_section="security",
    )


@settings_bp.route("/security/password", methods=["POST"])
@require_auth
def change_password():
    user = _current_user_or_abort()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    try:
        if not AuthService.verify_password(user, current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("settings.security"))
        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("settings.security"))
        AuthService.change_password(user, new_password)
        flash("Password changed successfully.", "success")
    except AuthError as exc:
        flash(str(exc), "error")
    return redirect(url_for("settings.security"))


# ---------------------------------------------------------------------------
# Connected accounts
# ---------------------------------------------------------------------------


@settings_bp.route("/accounts", methods=["GET"])
@require_auth
def accounts():
    user = _current_user_or_abort()
    connected = db.session.scalars(
        select(UserConnectedAccount).where(UserConnectedAccount.user_id == user.id)
    ).all()
    return render_template(
        "settings/accounts.html",
        profile_user=user,
        connected_accounts=connected,
        active_section="accounts",
    )


@settings_bp.route("/accounts/connect", methods=["POST"])
@require_auth
def connect_account():
    """Manually link a GitHub profile URL (no OAuth token)."""
    user = _current_user_or_abort()
    provider = request.form.get("provider", "").strip().lower()
    provider_username = request.form.get("provider_username", "").strip()
    profile_url = request.form.get("profile_url", "").strip()

    if not provider or provider not in {"github", "gitlab", "linkedin", "other"}:
        flash("Invalid provider.", "error")
        return redirect(url_for("settings.accounts"))

    try:
        profile_url_clean = (
            validate_url(profile_url, field="profile_url") if profile_url else None
        )
    except ValueError:
        flash("Invalid profile URL.", "error")
        return redirect(url_for("settings.accounts"))

    existing = db.session.scalar(
        select(UserConnectedAccount).where(
            UserConnectedAccount.user_id == user.id,
            UserConnectedAccount.provider == provider,
        )
    )
    if existing is not None:
        flash(f"You have already connected a {provider.capitalize()} account.", "error")
        return redirect(url_for("settings.accounts"))

    account = UserConnectedAccount(
        user_id=user.id,
        provider=provider,
        provider_username=provider_username or None,
        provider_profile_url=profile_url_clean,
    )
    db.session.add(account)
    db.session.commit()
    flash(f"{provider.capitalize()} account connected.", "success")
    return redirect(url_for("settings.accounts"))


@settings_bp.route("/accounts/disconnect", methods=["POST"])
@require_auth
def disconnect_account():
    user = _current_user_or_abort()
    account_id = request.form.get("account_id", type=int)
    if account_id is None:
        flash("Invalid request.", "error")
        return redirect(url_for("settings.accounts"))

    account = db.session.get(UserConnectedAccount, account_id)
    if account is None or account.user_id != user.id:
        flash("Account not found.", "error")
        return redirect(url_for("settings.accounts"))

    db.session.delete(account)
    db.session.commit()
    flash("Account disconnected.", "success")
    return redirect(url_for("settings.accounts"))


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


@settings_bp.route("/repositories", methods=["GET"])
@require_auth
def repositories():
    user = _current_user_or_abort()
    repos = RepositoryService.get_for_user(user.id)
    return render_template(
        "settings/repositories.html",
        profile_user=user,
        repositories=repos,
        active_section="repositories",
    )


@settings_bp.route("/repositories/add", methods=["POST"])
@require_auth
def add_repository():
    user = _current_user_or_abort()
    try:
        RepositoryService.add(
            user,
            repo_name=request.form.get("repo_name", ""),
            repo_url=request.form.get("repo_url", ""),
            description=request.form.get("description"),
            language=request.form.get("language"),
            is_featured="is_featured" in request.form,
            is_public="is_public" in request.form,
        )
        flash("Repository added.", "success")
    except RepositoryServiceError as exc:
        flash(exc.message, "error")
    return redirect(url_for("settings.repositories"))


@settings_bp.route("/repositories/<int:repo_id>/edit", methods=["POST"])
@require_auth
def edit_repository(repo_id: int):
    user = _current_user_or_abort()
    try:
        RepositoryService.update(
            repo_id,
            user.id,
            repo_name=request.form.get("repo_name"),
            repo_url=request.form.get("repo_url"),
            description=request.form.get("description"),
            language=request.form.get("language"),
            is_featured="is_featured" in request.form,
            is_public="is_public" in request.form,
        )
        flash("Repository updated.", "success")
    except RepositoryServiceError as exc:
        flash(exc.message, "error")
    return redirect(url_for("settings.repositories"))


@settings_bp.route("/repositories/<int:repo_id>/delete", methods=["POST"])
@require_auth
def delete_repository(repo_id: int):
    user = _current_user_or_abort()
    try:
        RepositoryService.delete(repo_id, user.id)
        flash("Repository removed.", "success")
    except RepositoryServiceError as exc:
        flash(exc.message, "error")
    return redirect(url_for("settings.repositories"))


@settings_bp.route("/repositories/reorder", methods=["POST"])
@require_auth
def reorder_repositories():
    """Accept JSON ``{"ids": [1, 3, 2, …]}`` and update sort_order."""
    from flask import jsonify  # noqa: PLC0415

    user = _current_user_or_abort()
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({"error": "ids must be a list of integers"}), 400
    try:
        RepositoryService.reorder(user.id, ids)
    except RepositoryServiceError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Contributions
# ---------------------------------------------------------------------------


@settings_bp.route("/contributions")
@require_auth
def contributions():
    user = _current_user_or_abort()
    revisions = db.session.scalars(
        select(Revision)
        .where(Revision.author_id == user.id)
        .order_by(desc(Revision.created_at))
        .limit(50)
    ).all()

    # Build a rendered identity for each revision
    revision_identities = {
        r.id: ContributionIdentityService.render_public(
            public_identity_mode=r.public_identity_mode,
            public_display_name_snapshot=r.public_display_name_snapshot,
            public_avatar_snapshot=r.public_avatar_snapshot,
            author=user,
        )
        for r in revisions
    }

    return render_template(
        "settings/contributions.html",
        profile_user=user,
        revisions=revisions,
        revision_identities=revision_identities,
        active_section="contributions",
    )


# ── Newsletter ─────────────────────────────────────────────────────────────────


@settings_bp.get("/newsletter")
@require_auth
def newsletter():
    """Show newsletter subscription status for the current user."""
    user = _current_user_or_abort()
    from backend.services.newsletter_service import NewsletterService  # noqa: PLC0415

    sub = NewsletterService.get_for_user(user.id)
    if sub is None:
        sub = NewsletterService.get_by_email(user.email)
    return render_template(
        "settings/newsletter.html",
        subscription=sub,
        active_section="newsletter",
    )


@settings_bp.post("/newsletter/subscribe")
@require_auth
def newsletter_subscribe():
    """Subscribe the logged-in user's email to the newsletter."""
    user = _current_user_or_abort()
    from backend.services.newsletter_service import NewsletterService  # noqa: PLC0415

    try:
        sub, confirm_token = NewsletterService.subscribe(
            user.email,
            source="settings",
            user_id=user.id,
        )
        db.session.commit()
        if sub.status == "pending":
            try:
                from flask import session as flask_session  # noqa: PLC0415

                from backend.tasks.email import (
                    send_newsletter_confirm_email,  # noqa: PLC0415
                )

                locale = flask_session.get("locale") or "en"
                send_newsletter_confirm_email.delay(user.email, confirm_token, locale)
            except Exception as exc:
                current_app.logger.warning("Newsletter confirm email failed: %s", exc)
            flash("Confirmation email sent — check your inbox.", "success")
        else:
            flash("You are already subscribed.", "info")
    except Exception as exc:
        current_app.logger.error("Newsletter subscribe error: %s", exc)
        flash("Could not process your request. Please try again.", "error")
    return redirect(url_for("settings.newsletter"))


@settings_bp.post("/newsletter/unsubscribe")
@require_auth
def newsletter_unsubscribe():
    """Unsubscribe the logged-in user from the newsletter."""
    user = _current_user_or_abort()
    from backend.services.newsletter_service import NewsletterService  # noqa: PLC0415

    sub = NewsletterService.get_for_user(user.id) or NewsletterService.get_by_email(
        user.email
    )
    if sub is None:
        flash("No active subscription found.", "info")
        return redirect(url_for("settings.newsletter"))

    from datetime import UTC, datetime  # noqa: PLC0415

    sub.status = "unsubscribed"
    sub.unsubscribed_at = datetime.now(UTC)
    db.session.commit()
    flash("You have been unsubscribed.", "success")
    return redirect(url_for("settings.newsletter"))
