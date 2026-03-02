"""SSR — Watch / Unwatch routes.

Routes
------
POST /posts/<slug>/watch               subscribe current user to a public post
POST /posts/<slug>/unwatch             unsubscribe from a public post
POST /w/<workspace_slug>/watch         subscribe to a workspace (member only)
POST /w/<workspace_slug>/unwatch       unsubscribe from a workspace
POST /w/<workspace_slug>/docs/<doc_slug>/watch    subscribe to a workspace doc
POST /w/<workspace_slug>/docs/<doc_slug>/unwatch  unsubscribe from a workspace doc

All routes require authentication.  Permission checks are delegated to
:func:`backend.services.notification_service.subscribe` which raises
:class:`~backend.services.notification_service.NotificationError` with an
appropriate HTTP status code on violations.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, request, url_for

from backend.services.notification_service import (
    NotificationError,
    subscribe,
    unsubscribe,
)
from backend.utils.auth import get_current_user, require_auth

watches_bp = Blueprint("watches", __name__)


# ── Public posts ──────────────────────────────────────────────────────────────


@watches_bp.post("/posts/<slug>/watch")
@require_auth
def watch_post(slug: str):
    """Subscribe the current user to a public post."""
    from backend.services.post_service import PostService  # noqa: PLC0415

    user = get_current_user()
    post = PostService.get_by_slug(slug)
    if post is None:
        flash("Post not found.", "error")
        return redirect(request.referrer or url_for("posts.post_detail", slug=slug))

    try:
        subscribe(user, "post", post.id)
        flash("You are now watching this post.", "success")
    except NotificationError as exc:
        flash(exc.message, "error")

    return redirect(request.referrer or url_for("posts.post_detail", slug=slug))


@watches_bp.post("/posts/<slug>/unwatch")
@require_auth
def unwatch_post(slug: str):
    """Unsubscribe the current user from a public post."""
    from backend.services.post_service import PostService  # noqa: PLC0415

    user = get_current_user()
    post = PostService.get_by_slug(slug)
    if post is None:
        flash("Post not found.", "error")
        return redirect(request.referrer or url_for("posts.post_detail", slug=slug))

    unsubscribe(user, "post", post.id)
    flash("You are no longer watching this post.", "info")
    return redirect(request.referrer or url_for("posts.post_detail", slug=slug))


# ── Workspaces ────────────────────────────────────────────────────────────────


@watches_bp.post("/w/<workspace_slug>/watch")
@require_auth
def watch_workspace(workspace_slug: str):
    """Subscribe the current user to a workspace (membership required)."""
    from backend.services import workspace_service as ws_svc  # noqa: PLC0415

    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)

    try:
        subscribe(user, "workspace", workspace.id)
        flash("You are now watching this workspace.", "success")
    except NotificationError as exc:
        flash(exc.message, "error")

    return redirect(
        request.referrer
        or url_for("workspace.dashboard", workspace_slug=workspace_slug)
    )


@watches_bp.post("/w/<workspace_slug>/unwatch")
@require_auth
def unwatch_workspace(workspace_slug: str):
    """Unsubscribe the current user from a workspace."""
    from backend.services import workspace_service as ws_svc  # noqa: PLC0415

    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)

    unsubscribe(user, "workspace", workspace.id)
    flash("You are no longer watching this workspace.", "info")
    return redirect(
        request.referrer
        or url_for("workspace.dashboard", workspace_slug=workspace_slug)
    )


# ── Workspace documents ───────────────────────────────────────────────────────


@watches_bp.post("/w/<workspace_slug>/docs/<doc_slug>/watch")
@require_auth
def watch_workspace_doc(workspace_slug: str, doc_slug: str):
    """Subscribe the current user to a workspace document (membership required)."""
    from backend.services import workspace_service as ws_svc  # noqa: PLC0415

    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    post = ws_svc.get_workspace_document(workspace, doc_slug)

    try:
        subscribe(user, "post", post.id)
        flash("You are now watching this document.", "success")
    except NotificationError as exc:
        flash(exc.message, "error")

    return redirect(
        request.referrer
        or url_for(
            "workspace.document",
            workspace_slug=workspace_slug,
            doc_slug=doc_slug,
        )
    )


@watches_bp.post("/w/<workspace_slug>/docs/<doc_slug>/unwatch")
@require_auth
def unwatch_workspace_doc(workspace_slug: str, doc_slug: str):
    """Unsubscribe the current user from a workspace document."""
    from backend.services import workspace_service as ws_svc  # noqa: PLC0415

    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    post = ws_svc.get_workspace_document(workspace, doc_slug)

    unsubscribe(user, "post", post.id)
    flash("You are no longer watching this document.", "info")
    return redirect(
        request.referrer
        or url_for(
            "workspace.document",
            workspace_slug=workspace_slug,
            doc_slug=doc_slug,
        )
    )


# ── Tags ──────────────────────────────────────────────────────────────────────


@watches_bp.post("/tags/<slug>/follow")
@require_auth
def follow_tag(slug: str):
    """Subscribe the current user to new posts tagged with *slug*.

    Any authenticated user may follow any tag (no membership required).
    Notifications are only delivered for PUBLIC posts — workspace posts
    tagged with the same tag are never surface to tag followers.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.tag import Tag  # noqa: PLC0415

    user = get_current_user()
    tag = db.session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is None:
        flash("Topic not found.", "error")
        return redirect(request.referrer or url_for("tags.tag_index"))

    try:
        subscribe(user, "tag", tag.id)
        flash(f"You are now following #{slug}.", "success")
    except NotificationError as exc:
        flash(exc.message, "error")

    return redirect(request.referrer or url_for("tags.tag_index"))


@watches_bp.post("/tags/<slug>/unfollow")
@require_auth
def unfollow_tag(slug: str):
    """Unsubscribe the current user from a tag."""
    from sqlalchemy import select  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.tag import Tag  # noqa: PLC0415

    user = get_current_user()
    tag = db.session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is None:
        flash("Topic not found.", "error")
        return redirect(request.referrer or url_for("tags.tag_index"))

    unsubscribe(user, "tag", tag.id)
    flash(f"You are no longer following #{slug}.", "info")
    return redirect(request.referrer or url_for("tags.tag_index"))
