"""SSR — public blog post views.

Routes
------
GET /posts/          paginated list of published posts
GET /posts/<slug>    full article view with rendered HTML
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from backend.models.post import PostStatus
from backend.routes.tags import _TAG_DESCRIPTIONS
from backend.services.analytics_service import AnalyticsService
from backend.services.post_service import RESERVED_SLUGS, PostError, PostService
from backend.services.post_version_service import PostVersionService
from backend.services.read_history_service import ReadHistoryService
from backend.services.release_notes_service import get_post_release_notes
from backend.utils.auth import get_current_user, require_auth
from backend.utils.diff import compute_diff, parse_diff_lines
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

    # Show "Updated" badges for authenticated users whose cached version is stale.
    user = get_current_user()
    updated_post_ids: frozenset[int] = frozenset()
    if user and posts:
        updated_post_ids = frozenset(
            ReadHistoryService.get_updated_post_ids(user.id, posts)
        )

    return render_template(
        "posts/list.html",
        posts=posts,
        page=page,
        pages=pages,
        total=total,
        per_page=_PER_PAGE,
        tag_slug=tag_slug,
        tag_description=_TAG_DESCRIPTIONS.get(tag_slug) if tag_slug else None,
        updated_post_ids=updated_post_ids,
    )


# ── /posts/new — writing surface ─────────────────────────────────────────────
# Declared *before* /<slug> so Flask never treats "new" as a slug.


@ssr_posts_bp.route("/new", methods=["GET", "POST"])
@require_auth
def new_post():
    """Create a new post and optionally publish it immediately."""
    user = get_current_user()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        markdown_body = request.form.get("markdown_body", "").strip()
        raw_tags = request.form.get("tags", "").strip()
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []
        seo_description = request.form.get("seo_description", "").strip() or None
        custom_slug = request.form.get("slug", "").strip() or None
        action = request.form.get("action", "draft")  # "draft" or "publish"

        try:
            # Validate custom slug if provided
            from backend.services.post_service import _slugify  # noqa: PLC0415

            if custom_slug:
                normalized = _slugify(custom_slug)
                if normalized in RESERVED_SLUGS:
                    flash(
                        f"Slug '{normalized}' is reserved — please choose another.",
                        "error",
                    )
                    return render_template("posts/new.html", form_data=request.form)
                custom_slug = normalized

            post = PostService.create(
                author_id=user.id,
                title=title,
                markdown_body=markdown_body,
                tags=tags,
                seo_description=seo_description,
            )

            # Override auto-generated slug if a valid custom one was provided
            if custom_slug and custom_slug != post.slug:
                from sqlalchemy import select  # noqa: PLC0415

                from backend.extensions import db  # noqa: PLC0415
                from backend.models.post import Post  # noqa: PLC0415

                clash = db.session.scalar(
                    select(Post.id)
                    .where(Post.slug == custom_slug)
                    .where(Post.id != post.id)
                )
                if clash is None:
                    post.slug = custom_slug
                    db.session.commit()

            if action == "publish":
                PostService.publish(post)
                flash("Post published!", "success")
            else:
                flash("Draft saved.", "success")

            return redirect(url_for("posts.post_detail", slug=post.slug))

        except PostError as exc:
            flash(str(exc), "error")
            return render_template("posts/new.html", form_data=request.form)

    return render_template("posts/new.html", form_data=None)


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

    # ── Read-history: snapshot the old read record, then upsert ─────────
    # We capture the version number *before* the upsert commits.
    # After commit SQLAlchemy expires ORM objects, so accessing attributes on the
    # old record would lazy-reload the *new* value — defeating the stale check.
    read_record = None
    last_read_version: int | None = None
    if user:
        read_record = ReadHistoryService.get_read(user.id, post.id)
        if read_record is not None:
            last_read_version = read_record.last_read_version
        ReadHistoryService.record_read(user.id, post)

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
    release_notes = get_post_release_notes(post.id)

    from backend.models.user import UserRole  # noqa: PLC0415
    from backend.services.content_link_service import (
        list_links_grouped,  # noqa: PLC0415
    )
    from backend.services.content_link_suggestion_service import (  # noqa: PLC0415
        suggest_for_post,
    )
    from backend.services.notification_service import is_subscribed  # noqa: PLC0415

    is_watching_post = is_subscribed(user, "post", post.id) if user else False

    links_grouped = list_links_grouped(post, workspace_id=None)
    can_manage_links = user is not None and user.role in (
        UserRole.editor,
        UserRole.admin,
    )
    link_suggestions = suggest_for_post(user, post, workspace_id=None)

    return render_template(
        "posts/detail.html",
        post=post,
        post_html=post_html,
        last_read_version=last_read_version,
        release_notes=release_notes,
        is_watching_post=is_watching_post,
        links_grouped=links_grouped,
        can_manage_links=can_manage_links,
        link_suggestions=link_suggestions,
        from_post=post,
    )


@ssr_posts_bp.route("/<slug>/edit", methods=["GET", "POST"])
@require_auth
def edit_post(slug: str):
    """Edit a post (author / admin / editor only)."""
    post = PostService.get_by_slug(slug)
    if post is None:
        abort(404)

    user = get_current_user()
    if user.id != post.author_id and user.role.value not in {"admin", "editor"}:
        abort(403)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        markdown_body = request.form.get("markdown_body", "").strip()
        raw_tags = request.form.get("tags", "").strip()
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []
        seo_title = request.form.get("seo_title", "").strip() or None
        seo_description = request.form.get("seo_description", "").strip() or None
        og_image_url = request.form.get("og_image_url", "").strip() or None

        try:
            PostService.update(
                post,
                title=title or None,
                markdown_body=markdown_body or None,
                tags=tags if tags else None,
                seo_title=seo_title,
                seo_description=seo_description,
                og_image_url=og_image_url,
            )
            flash("Post updated successfully.", "success")
            return redirect(url_for("posts.post_detail", slug=post.slug))
        except PostError as exc:
            flash(str(exc), "error")

    current_tags = ", ".join(t.slug for t in post.tags) if post.tags else ""
    return render_template("posts/edit.html", post=post, current_tags=current_tags)


@ssr_posts_bp.get("/<slug>/compare")
def compare(slug: str):
    """Show a version-to-version diff for a published post.

    Query params
    ------------
    from    : int, required — base (older) version number
    to      : int, optional — head (newer) version number; defaults to the
              post's current version when omitted, so ``?from=1`` compares
              v1 against the latest.  When *from* > *to* the values are
              swapped (and the template notes this to the user).
    context : int, optional — context lines in the diff (default 3, capped 0–99)
    """
    post = PostService.get_by_slug(slug)
    if post is None:
        abort(404)

    user = get_current_user()
    # Non-published posts only visible to author / editors
    if post.status != PostStatus.published:
        if user is None:
            abort(404)
        is_editor = user.role.value in {"admin", "editor"}
        if post.author_id != user.id and not is_editor:
            abort(404)

    # ── Parse & validate query params ────────────────────────────────────
    try:
        from_v = int(request.args["from"])
    except (KeyError, ValueError):
        abort(400)

    try:
        to_v = int(request.args.get("to", post.version))
    except ValueError:
        abort(400)

    # Normalise order; remember if a swap happened so the UI can tell the user
    was_swapped = from_v > to_v
    if was_swapped:
        from_v, to_v = to_v, from_v

    if from_v == to_v or from_v < 1 or to_v > post.version:
        abort(400)

    context = min(max(request.args.get("context", 3, type=int), 0), 99)

    # ── Fetch version snapshots ───────────────────────────────────────────
    old_md = PostVersionService.get_markdown_for_version(post.id, from_v)
    new_md = PostVersionService.get_markdown_for_version(post.id, to_v)

    versions_missing: list[int] = []
    if old_md is None:
        versions_missing.append(from_v)
    if new_md is None:
        versions_missing.append(to_v)

    diff_lines: list[dict] = []
    additions = 0
    deletions = 0

    if not versions_missing:
        diff_text = compute_diff(old_md, new_md, context=context)
        diff_lines = parse_diff_lines(diff_text)
        additions = sum(1 for ln in diff_lines if ln["kind"] == "add")
        deletions = sum(1 for ln in diff_lines if ln["kind"] == "del")

    return render_template(
        "posts/compare.html",
        post=post,
        from_version=from_v,
        to_version=to_v,
        context=context,
        diff_lines=diff_lines,
        additions=additions,
        deletions=deletions,
        versions_missing=versions_missing,
        was_swapped=was_swapped,
    )
