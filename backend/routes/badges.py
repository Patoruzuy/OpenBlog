"""SSR route — public badge catalogue."""

from flask import Blueprint, make_response, render_template

from backend.services.badge_service import BadgeService

ssr_badges_bp = Blueprint("badges_catalog", __name__)


@ssr_badges_bp.get("/badges")
def badge_catalog() -> object:
    """Display all badge definitions grouped by category."""
    groups = BadgeService.list_definitions_by_category()
    resp = make_response(render_template("badges/catalog.html", groups=groups))
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp
