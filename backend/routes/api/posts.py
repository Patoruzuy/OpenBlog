"""JSON API — post CRUD endpoints.

All endpoints are CSRF-exempt (Bearer tokens provide equivalent protection).
Rate limits are inherited from the app-level default (none) unless explicitly
set on a route.

Routes
------
GET    /api/posts/               list posts (published; editors/admins see all)
POST   /api/posts/               create draft  [contributor+]
GET    /api/posts/<slug>         get single post
PUT    /api/posts/<slug>         update post   [author or editor/admin]
DELETE /api/posts/<slug>         archive post  [author or admin]
POST   /api/posts/<slug>/publish publish/schedule post  [editor/admin]
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import func, select

from backend.extensions import csrf, db
from backend.models.comment import Comment
from backend.models.post import PostStatus
from backend.models.user import UserRole
from backend.services.bookmark_service import BookmarkService
from backend.services.post_service import PostError, PostService
from backend.services.vote_service import VoteService
from backend.utils.auth import api_require_auth, api_require_role, get_current_user
from backend.utils.markdown import get_rendered_html
from backend.schemas import CreatePostSchema, PublishPostSchema, UpdatePostSchema, load_json

api_posts_bp = Blueprint("api_posts", __name__, url_prefix="/api/posts")
csrf.exempt(api_posts_bp)

# Role sets are defined centrally on UserRole; local aliases for readability.
_EDITOR_ROLES = UserRole.EDITOR_SET
_AUTHOR_ROLES = UserRole.AUTHOR_SET


# ── Helpers ───────────────────────────────────────────────────────────────────


def _post_dict(
    post,
    *,
    include_body: bool = False,
    viewer_id: int | None = None,
    # Pre-fetched batch data — supplied by list_posts() to avoid N+1 queries.
    # Single-post endpoints leave these as None and fall back to per-post queries.
    _comment_counts: dict[int, int] | None = None,
    _vote_counts: dict[int, int] | None = None,
    _voted_ids: set[int] | None = None,
    _bookmarked_ids: set[int] | None = None,
) -> dict:
    comment_count = (
        _comment_counts.get(post.id, 0)
        if _comment_counts is not None
        else (
            db.session.scalar(
                select(func.count(Comment.id)).where(
                    Comment.post_id == post.id,
                    Comment.is_deleted.is_(False),
                )
            )
            or 0
        )
    )
    vote_count = (
        _vote_counts[post.id]
        if _vote_counts is not None
        else VoteService.vote_count("post", post.id)
    )
    d: dict = {
        "id": post.id,
        "slug": post.slug,
        "title": post.title,
        "status": post.status.value,
        "version": post.version,
        "is_featured": bool(post.is_featured),
        "reading_time_minutes": post.reading_time_minutes,
        "view_count": post.view_count,
        "comment_count": comment_count,
        "vote_count": vote_count,
        "author": {
            "id": post.author_id,
            "username": post.author.username,
            "display_name": post.author.display_name,
        },
        "tags": [
            {"id": t.id, "name": t.name, "slug": t.slug} for t in post.tags
        ],
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "og_image_url": post.og_image_url,
        "publish_at": post.publish_at.isoformat() if post.publish_at else None,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "created_at": post.created_at.isoformat(),
        "updated_at": post.updated_at.isoformat(),
    }
    if viewer_id is not None:
        d["has_voted"] = (
            post.id in _voted_ids
            if _voted_ids is not None
            else VoteService.has_voted(viewer_id, "post", post.id)
        )
        d["has_bookmarked"] = (
            post.id in _bookmarked_ids
            if _bookmarked_ids is not None
            else BookmarkService.has_bookmarked(viewer_id, post.id)
        )
    if include_body:
        d["markdown_body"] = post.markdown_body
        d["rendered_html"] = get_rendered_html(post.id, post.markdown_body)
    return d


def _can_edit(post, user) -> bool:
    """True if *user* is the author or has editor/admin role."""
    return post.author_id == user.id or user.role.value in _EDITOR_ROLES


# ── Preview (markdown → HTML) ─────────────────────────────────────────────────


@api_posts_bp.post("/preview")
@api_require_auth
def preview_markdown():
    """Render a markdown snippet to HTML without saving anything.

    Request body (JSON)
    -------------------
    markdown  str  required

    Response
    --------
    {"html": "<p>...</p>"}
    """
    data = request.get_json(silent=True) or {}
    markdown_body = data.get("markdown", "")
    if not isinstance(markdown_body, str):
        return jsonify({"error": "markdown must be a string"}), 400

    from backend.utils.markdown import render_markdown  # noqa: PLC0415

    html = render_markdown(markdown_body)
    return jsonify({"html": html})


# ── List ──────────────────────────────────────────────────────────────────────


@api_posts_bp.get("/")
def list_posts():
    """Return a paginated list of posts.

    Query params
    ------------
    page     int   (default 1)
    per_page int   (default 20, max 100)
    tag      str   slug of a tag to filter by
    """
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))
    tag_slug = request.args.get("tag") or None

    user = get_current_user()
    if user is not None and user.role.value in _EDITOR_ROLES:
        posts, total = PostService.list_all(page, per_page, tag_slug)
    else:
        posts, total = PostService.list_published(page, per_page, tag_slug)

    viewer_id = user.id if user is not None else None

    # ── Batch pre-fetch to eliminate N+1 queries ──────────────────────────────
    post_ids = [p.id for p in posts]
    comment_counts: dict[int, int] = {}
    if post_ids:
        rows = db.session.execute(
            select(Comment.post_id, func.count(Comment.id).label("cnt"))
            .where(
                Comment.post_id.in_(post_ids),
                Comment.is_deleted.is_(False),
            )
            .group_by(Comment.post_id)
        ).all()
        comment_counts = {row.post_id: row.cnt for row in rows}
    vote_counts_map = VoteService.vote_counts("post", post_ids)
    voted_ids: set[int] = (
        VoteService.voted_set(viewer_id, "post", post_ids)
        if viewer_id is not None
        else set()
    )
    bookmarked_ids: set[int] = (
        BookmarkService.bookmarked_set(viewer_id, post_ids)
        if viewer_id is not None
        else set()
    )
    # ─────────────────────────────────────────────────────────────────────────

    return jsonify(
        {
            "posts": [
                _post_dict(
                    p,
                    viewer_id=viewer_id,
                    _comment_counts=comment_counts,
                    _vote_counts=vote_counts_map,
                    _voted_ids=voted_ids,
                    _bookmarked_ids=bookmarked_ids,
                )
                for p in posts
            ],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if total else 0,
        }
    )


# ── Create ────────────────────────────────────────────────────────────────────


@api_posts_bp.post("/")
@api_require_role("admin", "editor", "contributor")
def create_post():
    """Create a new draft post.

    Request body (JSON)
    -------------------
    title           str  required
    markdown_body   str  optional (default "")
    tags            list[str]  optional
    seo_title       str  optional
    seo_description str  optional
    og_image_url    str  optional
    """
    data, err = load_json(CreatePostSchema())
    if err:
        return err

    user = get_current_user()
    try:
        post = PostService.create(
            author_id=user.id,
            title=data["title"].strip(),
            markdown_body=data["markdown_body"],
            tags=data["tags"],
            seo_title=data["seo_title"],
            seo_description=data["seo_description"],
            og_image_url=data["og_image_url"],
        )
    except PostError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(_post_dict(post, include_body=True, viewer_id=user.id)), 201


# ── Get single post ───────────────────────────────────────────────────────────


@api_posts_bp.get("/<slug>")
def get_post(slug: str):
    """Return a single post by slug.

    Drafts and archived posts are hidden from anonymous users.
    Authors can see their own drafts; editors/admins can see all.
    """
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    if post.status != PostStatus.published:
        if user is None:
            return jsonify({"error": "Post not found."}), 404
        if not _can_edit(post, user):
            return jsonify({"error": "Post not found."}), 404

    viewer_id = user.id if user is not None else None
    return jsonify(_post_dict(post, include_body=True, viewer_id=viewer_id))


@api_posts_bp.put("/<slug>")
@api_require_auth
def update_post(slug: str):
    """Update a post.  Authors can edit their own; editors/admins can edit any.

    Request body (JSON) — all fields optional
    ------------------------------------------
    title           str
    markdown_body   str
    tags            list[str]
    seo_title       str
    seo_description str
    og_image_url    str
    """
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    if not _can_edit(post, user):
        return jsonify({"error": "Insufficient permissions."}), 403

    data, err = load_json(UpdatePostSchema())
    if err:
        return err
    try:
        post = PostService.update(
            post,
            title=data["title"],
            markdown_body=data["markdown_body"],
            tags=data["tags"],
            seo_title=data["seo_title"],
            seo_description=data["seo_description"],
            og_image_url=data["og_image_url"],
        )
    except PostError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(_post_dict(post, include_body=True, viewer_id=user.id))


# ── Archive (soft-delete) ─────────────────────────────────────────────────────


@api_posts_bp.delete("/<slug>")
@api_require_auth
def delete_post(slug: str):
    """Archive a post.  Authors can archive their own; admins can archive any."""
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    # Only the author or an admin can archive.
    if post.author_id != user.id and user.role.value != UserRole.admin.value:
        return jsonify({"error": "Insufficient permissions."}), 403

    PostService.archive(post)
    return jsonify({"message": "Post archived.", "slug": slug})


# ── Publish / Schedule ────────────────────────────────────────────────────────


@api_posts_bp.post("/<slug>/autosave")
@api_require_auth
def autosave_post(slug: str):
    """Background autosave endpoint for draft posts.

    Accepts partial updates (title, markdown_body, excerpt, tags) and an
    ``autosave_revision`` optimistic-concurrency token.  Returns the new
    revision number so the client can synchronise its local counter.

    Responses
    ---------
    200  { ok, post_id, slug, autosave_revision, saved_at_iso }
    409  { conflict, autosave_revision, slug, saved_at_iso }  — revision mismatch
    422  { error }  — post is not a draft
    403  { error }  — not the author (or editor/admin)
    404  { error }  — post not found
    """
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    user = get_current_user()
    if not _can_edit(post, user):
        return jsonify({"error": "Insufficient permissions."}), 403

    body = request.get_json(silent=True) or {}
    title = body.get("title")
    markdown_body = body.get("markdown_body")
    excerpt = body.get("excerpt")
    raw_tags = body.get("tags")
    tags: list[str] | None = None
    if raw_tags is not None:
        if isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags]
        elif isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

    # Validate sizes
    if title is not None and len(str(title)) > 512:
        return jsonify({"error": "Title too long (max 512 chars)."}), 400
    if markdown_body is not None and len(str(markdown_body)) > 500_000:
        return jsonify({"error": "Body too large (max 500 000 chars)."}), 400

    try:
        client_revision = int(body.get("autosave_revision", 0))
    except (TypeError, ValueError):
        client_revision = 0

    try:
        post = PostService.autosave(
            post,
            title=title,
            markdown_body=markdown_body,
            excerpt=excerpt,
            tags=tags,
            client_revision=client_revision,
        )
    except PostError as exc:
        if exc.status_code == 409:
            return jsonify({
                "conflict": True,
                "autosave_revision": post.autosave_revision,
                "slug": post.slug,
                "saved_at_iso": (
                    post.last_autosaved_at.isoformat()
                    if post.last_autosaved_at else None
                ),
            }), 409
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify({
        "ok": True,
        "post_id": post.id,
        "slug": post.slug,
        "autosave_revision": post.autosave_revision,
        "saved_at_iso": post.last_autosaved_at.isoformat() if post.last_autosaved_at else None,
    }), 200


@api_posts_bp.post("/<slug>/publish")
@api_require_role("admin", "editor")
def publish_post(slug: str):
    """Publish immediately, or schedule for a future UTC datetime.

    Request body (JSON) — all optional
    ------------------------------------
    publish_at   str   ISO-8601 UTC datetime — if provided, schedules instead
    """
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    data, err = load_json(PublishPostSchema())
    if err:
        return err
    publish_at: datetime | None = None
    if raw := data.get("publish_at"):
        try:
            publish_at = datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return jsonify({"error": "publish_at must be an ISO-8601 datetime string."}), 400

    try:
        post = PostService.publish(post, at=publish_at)
    except PostError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    user = get_current_user()
    return jsonify(_post_dict(post, viewer_id=user.id if user else None))
