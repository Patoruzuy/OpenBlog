"""SSR routes for the AI Review Engine.

URL structure
-------------
POST /w/<workspace_slug>/docs/<doc_slug>/ai-review
    Submit a review request for the current post body.
    Any workspace member may request; redirect back with flash.

POST /w/<workspace_slug>/revisions/<int:revision_id>/ai-review
    Submit a review request scoped to a specific revision diff.

POST /w/<workspace_slug>/ai-reviews/<int:request_id>/cancel
    Cancel a queued/running review (requester or editor/owner).

Cache policy
------------
The blueprint registers an ``after_request`` hook matching the workspace
blueprint's ``private, no-store`` contract so no AI content is cached by
intermediaries.

Error mapping
-------------
:class:`~backend.services.ai_review_service.AIReviewError` is caught and
converted to flash + redirect or abort as appropriate.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    request,
    url_for,
)

from backend.extensions import db
from backend.services import ai_review_service as ai_svc
from backend.services import workspace_service as ws_svc
from backend.utils.auth import get_current_user, require_auth

ai_reviews_bp = Blueprint("ai_reviews", __name__, url_prefix="/w")


# ── Blueprint-wide Cache-Control ──────────────────────────────────────────────


@ai_reviews_bp.after_request
def _no_store(response):
    """Match the workspace blueprint cache policy (private, no-store)."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers.pop("Expires", None)
    response.headers.pop("Pragma", None)
    return response


# ── Submit review for a workspace doc ────────────────────────────────────────


@ai_reviews_bp.post("/<workspace_slug>/docs/<doc_slug>/ai-review")
@require_auth
def request_doc_review(workspace_slug: str, doc_slug: str):
    """Submit an AI review for the current document body.

    Any workspace member may trigger this.  Redirects back to the document
    page with a flash message indicating the queued status (or an error).
    """
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)

    post = ws_svc.get_workspace_document(workspace, doc_slug)
    if post is None:
        abort(404)

    review_type = request.form.get("review_type", "full").strip().lower()

    doc_url = url_for(
        "workspace.document",
        workspace_slug=workspace_slug,
        doc_slug=doc_slug,
        _anchor="ai-review",
    )

    try:
        ai_req = ai_svc.request_review(
            user=user,
            post=post,
            revision=None,
            review_type=review_type,
        )
    except ai_svc.AIReviewError as exc:
        if exc.status_code == 429:
            flash(exc.message, "warning")
        elif exc.status_code == 404:
            abort(404)
        else:
            flash(exc.message, "error")
        return redirect(doc_url)

    from backend.models.ai_review import AIReviewStatus  # noqa: PLC0415

    if ai_req.status == AIReviewStatus.completed.value:
        flash("AI review already available — showing cached result.", "info")
    elif ai_req.status == AIReviewStatus.queued.value:
        flash("AI review queued. Refresh in a moment to see results.", "success")
    elif ai_req.status == AIReviewStatus.running.value:
        flash("AI review already in progress.", "info")
    else:
        flash("AI review submitted.", "success")

    return redirect(doc_url)


# ── Submit review for a proposed revision ────────────────────────────────────


@ai_reviews_bp.post("/<workspace_slug>/revisions/<int:revision_id>/ai-review")
@require_auth
def request_revision_review(workspace_slug: str, revision_id: int):
    """Submit an AI review scoped to a proposed revision's diff.

    The review uses the unified diff between the current post body and the
    proposed markdown so the AI focuses on the change rather than the whole
    document.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from backend.extensions import db as _db  # noqa: PLC0415
    from backend.models.revision import Revision  # noqa: PLC0415

    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)

    revision: Revision | None = _db.session.scalar(
        select(Revision).where(Revision.id == revision_id)
    )
    if revision is None:
        abort(404)

    # Load the post and verify it belongs to this workspace.
    from backend.models.post import Post  # noqa: PLC0415

    post = _db.session.get(Post, revision.post_id)
    if post is None or post.workspace_id != workspace.id:
        abort(404)

    review_type = request.form.get("review_type", "full").strip().lower()

    # Redirect target: revision list for the document.
    fallback_url = url_for(
        "workspace.document",
        workspace_slug=workspace_slug,
        doc_slug=post.slug,
        _anchor="ai-review",
    )

    try:
        ai_req = ai_svc.request_review(
            user=user,
            post=post,
            revision=revision,
            review_type=review_type,
        )
    except ai_svc.AIReviewError as exc:
        if exc.status_code == 429:
            flash(exc.message, "warning")
        elif exc.status_code == 404:
            abort(404)
        else:
            flash(exc.message, "error")
        return redirect(fallback_url)

    from backend.models.ai_review import AIReviewStatus  # noqa: PLC0415

    if ai_req.status == AIReviewStatus.completed.value:
        flash("AI review (diff) already available — showing cached result.", "info")
    else:
        flash("AI review of revision queued. Refresh to see results.", "success")

    return redirect(fallback_url)


# ── Cancel a review ───────────────────────────────────────────────────────────


@ai_reviews_bp.post("/<workspace_slug>/ai-reviews/<int:request_id>/cancel")
@require_auth
def cancel_review(workspace_slug: str, request_id: int):
    """Cancel a queued or running AI review.

    The redirect target is the document page; extracts post slug from the
    request row.
    """
    user = get_current_user()
    # Verify workspace membership first (get_workspace_for_user aborts 404).
    ws_svc.get_workspace_for_user(workspace_slug, user)

    try:
        ai_req = ai_svc.cancel_review(request_id, user)
    except ai_svc.AIReviewError as exc:
        if exc.status_code == 403:
            abort(403)
        elif exc.status_code == 404:
            abort(404)
        flash(exc.message, "error")
        return redirect(url_for("workspace.dashboard", workspace_slug=workspace_slug))

    from backend.models.post import Post  # noqa: PLC0415

    post = db.session.get(Post, ai_req.post_id)
    if post is None:
        return redirect(
            url_for("workspace.dashboard", workspace_slug=workspace_slug)
        )

    flash("AI review canceled.", "info")
    return redirect(
        url_for(
            "workspace.document",
            workspace_slug=workspace_slug,
            doc_slug=post.slug,
            _anchor="ai-review",
        )
    )


# ── Create revision from AI suggestion ───────────────────────────────────────


@ai_reviews_bp.post(
    "/<workspace_slug>/docs/<doc_slug>/ai-review/<int:request_id>"
    "/suggestions/<suggestion_id>/create-revision"
)
@require_auth
def create_revision_from_suggestion(
    workspace_slug: str,
    doc_slug: str,
    request_id: int,
    suggestion_id: str,
):
    """Convert a single AI suggested edit into a pending Revision proposal.

    Contributors and above may trigger this.  The resulting revision is always
    *pending* — it must be accepted by an editor or owner through the normal
    revision workflow.

    On success: redirects to the document page anchored at ``#revisions``.
    On error:   flashes a message and redirects back to ``#ai-review``.
    """
    from backend.services import ai_revision_service as ai_rev_svc  # noqa: PLC0415

    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)

    post = ws_svc.get_workspace_document(workspace, doc_slug)
    if post is None:
        abort(404)

    doc_url = url_for(
        "workspace.document",
        workspace_slug=workspace_slug,
        doc_slug=doc_slug,
        _anchor="ai-review",
    )

    try:
        ai_rev_svc.create_revision_from_ai_suggestion(
            user=user,
            post=post,
            ai_review_request_id=request_id,
            suggestion_id=suggestion_id,
        )
    except ai_rev_svc.AIRevisionError as exc:
        if exc.status_code == 403:
            abort(403)
        elif exc.status_code == 404:
            abort(404)
        flash(exc.message, "error")
        return redirect(doc_url)

    flash("Revision created from AI suggestion.", "success")
    return redirect(
        url_for(
            "workspace.document",
            workspace_slug=workspace_slug,
            doc_slug=doc_slug,
            _anchor="revisions",
        )
    )
