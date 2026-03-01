"""SSR — revision review surface.

Routes
------
GET  /revisions/         pending revision queue (public read-only view)
GET  /revisions/<id>     single revision with structured diff
POST /revisions/<id>/accept   accept [editor/admin]
POST /revisions/<id>/reject   reject [editor/admin]
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from backend.extensions import db
from backend.services.post_service import PostService
from backend.services.revision_service import RevisionError, RevisionService
from backend.utils.auth import get_current_user, require_auth, require_role
from backend.utils.diff import parse_diff_lines as _parse_diff_lines

ssr_revisions_bp = Blueprint("revisions", __name__, url_prefix="/revisions")


@ssr_revisions_bp.get("/")
def revision_list():
    """Show the pending revision queue."""
    page = max(1, request.args.get("page", 1, type=int))
    status_filter = request.args.get("status", "pending")

    if status_filter == "all":
        # List all revisions across posts — reuse list_pending logic with a broader query
        from sqlalchemy import select

        from backend.models.revision import Revision as Rev

        per_page = 20
        from sqlalchemy import func

        q = select(Rev).order_by(Rev.created_at.desc())
        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        revisions = list(
            db.session.scalars(q.offset((page - 1) * per_page).limit(per_page)).all()
        )
        pages = (total + per_page - 1) // per_page if total else 0
    else:
        revisions, total = RevisionService.list_pending(page=page, per_page=20)
        pages = (total + 20 - 1) // 20 if total else 0

    return render_template(
        "revisions/list.html",
        revisions=revisions,
        total=total,
        page=page,
        pages=pages,
        status_filter=status_filter,
    )


@ssr_revisions_bp.get("/<int:revision_id>")
def revision_detail(revision_id: int):
    """Show a single revision with structured diff."""
    revision = RevisionService.get_by_id(revision_id)
    if revision is None:
        abort(404)

    user = get_current_user()
    can_review = user is not None and user.role.value in {"admin", "editor"}

    # Compute/retrieve the diff
    try:
        diff_text = RevisionService.get_diff(revision_id)
    except RevisionError:
        diff_text = ""

    diff_lines = _parse_diff_lines(diff_text)

    # Count additions / deletions for the summary bar
    additions = sum(1 for ln in diff_lines if ln["kind"] == "add")
    deletions = sum(1 for ln in diff_lines if ln["kind"] == "del")

    # Staleness check
    from backend.models.post import Post

    post_obj = db.session.get(Post, revision.post_id)
    is_stale = post_obj is not None and post_obj.version > revision.base_version_number

    return render_template(
        "revisions/detail.html",
        revision=revision,
        post=post_obj,
        diff_lines=diff_lines,
        additions=additions,
        deletions=deletions,
        is_stale=is_stale,
        can_review=can_review,
    )


@ssr_revisions_bp.post("/<int:revision_id>/accept")
@require_role("admin", "editor")
def accept_revision(revision_id: int):
    """Accept a pending revision."""
    user = get_current_user()
    revision = RevisionService.get_by_id(revision_id)
    if revision is None:
        abort(404)
    try:
        RevisionService.accept(revision_id, reviewer_id=user.id)
        flash("Revision accepted and post updated.", "success")
    except RevisionError as exc:
        flash(str(exc), "error")
    return redirect(url_for("revisions.revision_detail", revision_id=revision_id))


@ssr_revisions_bp.post("/<int:revision_id>/reject")
@require_role("admin", "editor")
def reject_revision(revision_id: int):
    """Reject a pending revision."""
    user = get_current_user()
    revision = RevisionService.get_by_id(revision_id)
    if revision is None:
        abort(404)
    note = (request.form.get("rejection_note") or "").strip()
    try:
        RevisionService.reject(revision_id, reviewer_id=user.id, note=note)
        flash("Revision rejected.", "success")
    except RevisionError as exc:
        flash(str(exc), "error")
    return redirect(url_for("revisions.revision_detail", revision_id=revision_id))


@ssr_revisions_bp.route("/submit/<slug>", methods=["GET", "POST"])
@require_auth
def submit_revision(slug: str):
    """Submit a revision proposal for a published post."""
    from backend.models.post import PostStatus

    post = PostService.get_by_slug(slug)
    if post is None:
        abort(404)

    user = get_current_user()

    # Authors use the edit page; only non-authors can submit revisions.
    if user.id == post.author_id:
        flash("You own this post — use the edit page instead.", "info")
        return redirect(url_for("posts.edit_post", slug=slug))

    if post.status != PostStatus.published:
        abort(404)

    if request.method == "POST":
        proposed_markdown = request.form.get("proposed_markdown", "").strip()
        summary = request.form.get("summary", "").strip()

        try:
            revision = RevisionService.submit(
                post_id=post.id,
                author_id=user.id,
                proposed_markdown=proposed_markdown,
                summary=summary,
            )
            flash("Your revision has been submitted for review.", "success")
            return redirect(
                url_for("revisions.revision_detail", revision_id=revision.id)
            )
        except RevisionError as exc:
            flash(str(exc), "error")

    return render_template("revisions/submit.html", post=post)
