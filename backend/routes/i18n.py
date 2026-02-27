"""Language-selection route.

GET /lang/<locale>
    Persist the requested locale in the session and redirect back to the
    referring page (or the homepage if no Referer header is present).

Only locales listed in SUPPORTED_LOCALES (config) are accepted; unsupported
values are silently ignored and the user is redirected without any change.
"""

from __future__ import annotations

from flask import Blueprint, current_app, redirect, request, session

i18n_bp = Blueprint("i18n", __name__)


@i18n_bp.get("/lang/<locale>")
def set_lang(locale: str):
    """Persist *locale* in the session if it is a supported value."""
    supported: list[str] = current_app.config.get("SUPPORTED_LOCALES", ["en", "es"])
    if locale in supported:
        session["locale"] = locale
    # Redirect back to the page the user came from, or fall back to home.
    return redirect(request.referrer or "/")
