"""SSR Drafts dashboard — lists the current user's draft posts."""

from __future__ import annotations

import math

from flask import Blueprint, abort, redirect, render_template, request, url_for

from backend.services.post_service import PostService
from backend.utils.auth import get_current_user, require_auth

ssr_drafts_bp = Blueprint("drafts", __name__, url_prefix="/drafts")


@ssr_drafts_bp.get("/")
@require_auth
def drafts_index():
    """Render the authenticated user's drafts dashboard."""
    viewer = get_current_user()
    search = request.args.get("search", "").strip() or None
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20

    posts, total = PostService.list_drafts_by_author(
        viewer.id, page=page, per_page=per_page, search=search
    )
    pages = math.ceil(total / per_page) if total else 0

    return render_template(
        "drafts/index.html",
        posts=posts,
        total=total,
        pages=pages,
        page=page,
        search=search or "",
    )


@ssr_drafts_bp.post("/<slug>/delete")
@require_auth
def delete_draft(slug: str):
    """Hard-delete a draft post owned by the current user."""
    viewer = get_current_user()
    post = PostService.get_by_slug(slug)
    if post is None or post.author_id != viewer.id:
        abort(404)
    PostService.delete(post)
    return redirect(url_for("drafts.drafts_index"))
