"""Static content pages blueprint.

Serves informational pages that have no dynamic data:
about, privacy, terms of service, cookie policy,
editorial policy, changelog, and contact.
"""

from __future__ import annotations

from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__, url_prefix="/pages")


@pages_bp.get("/about")
def about():
    return render_template("pages/about.html")


@pages_bp.get("/contact")
def contact():
    return render_template("pages/contact.html")


@pages_bp.get("/privacy")
def privacy():
    return render_template("pages/privacy.html")


@pages_bp.get("/terms")
def terms():
    return render_template("pages/terms.html")


@pages_bp.get("/cookies")
def cookies():
    return render_template("pages/cookies.html")


@pages_bp.get("/editorial-policy")
def editorial_policy():
    return render_template("pages/editorial_policy.html")


@pages_bp.get("/changelog")
def changelog():
    return render_template("pages/changelog.html")
