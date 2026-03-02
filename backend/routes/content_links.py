"""Content-link routes — add / remove Knowledge Graph edges.

Endpoints
---------
POST /links/add             Add a relationship (editor+)
POST /links/<id>/delete     Remove a relationship (editor+)

Both endpoints redirect back to the ``next`` form field.
Authentication is required; permissions are enforced by the service layer.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    request,
)

from backend.extensions import db
from backend.models.post import Post
from backend.services.content_link_service import (
    ContentLinkError,
    add_link,
    get_link_or_none,
    remove_link,
)
from backend.utils.auth import get_current_user, require_auth

content_links_bp = Blueprint("content_links", __name__)

_FALLBACK = "/"


def _next_url() -> str:
    nxt = request.form.get("next", "").strip()
    # Basic open-redirect guard: only allow relative paths.
    if nxt and nxt.startswith("/"):
        return nxt
    return _FALLBACK


@content_links_bp.post("/links/add")
@require_auth
def add_content_link():
    """Add a directed relationship between two posts."""
    user = get_current_user()

    from_post_id_raw = request.form.get("from_post_id", "").strip()
    to_post_slug = request.form.get("to_post_slug", "").strip()
    link_type = request.form.get("link_type", "").strip()
    nxt = _next_url()

    if not from_post_id_raw or not to_post_slug or not link_type:
        flash("from_post_id, to_post_slug, and link_type are all required.", "error")
        return redirect(nxt)

    try:
        from_post_id = int(from_post_id_raw)
    except ValueError:
        flash("Invalid from_post_id.", "error")
        return redirect(nxt)

    from_post = db.session.get(Post, from_post_id)
    if from_post is None:
        abort(404)

    # Resolve to_post by slug — search globally (both public+workspace layers)
    # because the scope validation is done inside the service.
    from sqlalchemy import select

    to_post = db.session.execute(
        select(Post).where(Post.slug == to_post_slug)
    ).scalar_one_or_none()
    if to_post is None:
        flash(f"No post found with slug '{to_post_slug}'.", "error")
        return redirect(nxt)

    try:
        add_link(user, from_post, to_post, link_type)
        db.session.commit()
        flash("Relationship added.", "success")
    except ContentLinkError as exc:
        flash(str(exc), "error")

    return redirect(nxt)


@content_links_bp.post("/links/<int:link_id>/delete")
@require_auth
def delete_content_link(link_id: int):
    """Remove a content link."""
    user = get_current_user()
    nxt = _next_url()

    link = get_link_or_none(link_id)
    if link is None:
        flash("Relationship not found.", "error")
        return redirect(nxt)

    try:
        remove_link(user, link_id)
        db.session.commit()
        flash("Relationship removed.", "success")
    except ContentLinkError as exc:
        flash(str(exc), "error")

    return redirect(nxt)
