"""SSR — public blog post views.

Routes
------
GET /posts/          paginated list of published posts
GET /posts/<slug>    full article view with rendered HTML
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request

from backend.models.post import PostStatus
from backend.services.analytics_service import AnalyticsService
from backend.services.post_service import PostService
from backend.utils.auth import get_current_user
from backend.utils.markdown import (  # noqa: F401
    get_rendered_html,
    invalidate_html_cache,
)

ssr_posts_bp = Blueprint("posts", __name__, url_prefix="/posts")

_PER_PAGE = 15


@ssr_posts_bp.get("/")
def list_posts():
    page = max(1, request.args.get("page", 1, type=int))
    tag_slug = request.args.get("tag") or None

    posts, total = PostService.list_published(page, _PER_PAGE, tag_slug)
    pages = (total + _PER_PAGE - 1) // _PER_PAGE if total else 0

    return render_template(
        "posts/list.html",
        posts=posts,
        page=page,
        pages=pages,
        total=total,
        tag_slug=tag_slug,
    )


@ssr_posts_bp.get("/<slug>")
def post_detail(slug: str):
    post = PostService.get_by_slug(slug)
    if post is None:
        abort(404)

    user = get_current_user()

    # Non-published posts are only visible to the author and editors/admins.
    if post.status != PostStatus.published:
        if user is None:
            abort(404)
        is_editor = user.role.value in {"admin", "editor"}
        if post.author_id != user.id and not is_editor:
            abort(404)

    # Increment view count and queue an analytics event (both best-effort).
    from backend.extensions import db
    post.view_count += 1
    db.session.commit()

    AnalyticsService.queue_event(
        "post_view",
        post_id=post.id,
        user_id=user.id if user else None,
        session_id=request.cookies.get("session"),
        referrer=request.referrer,
        user_agent=request.headers.get("User-Agent"),
    )

    post_html = get_rendered_html(post.id, post.markdown_body)

    return render_template(
        "posts/detail.html",
        post=post,
        post_html=post_html,
    )
