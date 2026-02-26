"""JSON API — revision workflow (contributor edit proposals).

Routes
------
POST   /api/posts/<slug>/revisions        submit a revision        [authenticated]
GET    /api/posts/<slug>/revisions        list revisions for post  [editor, admin]
GET    /api/revisions/pending             editor review queue      [editor, admin]
GET    /api/revisions/<id>                single revision detail   [editor, admin]
GET    /api/revisions/<id>/diff           unified diff             [editor, admin]
POST   /api/revisions/<id>/accept         accept a revision        [editor, admin]
POST   /api/revisions/<id>/reject         reject a revision        [editor, admin]

Staleness flag
--------------
The ``is_stale`` field in every serialised revision is True when
``post.version > revision.base_version_number``, indicating that another
revision was accepted after this one was submitted.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.extensions import csrf
from backend.models.revision import RevisionStatus
from backend.services.post_service import PostService
from backend.services.revision_service import RevisionError, RevisionService
from backend.utils.auth import api_require_auth, api_require_role, get_current_user
from backend.schemas import RejectRevisionSchema, SubmitRevisionSchema, load_json

api_revisions_bp = Blueprint("api_revisions", __name__, url_prefix="/api")
csrf.exempt(api_revisions_bp)


# ── Serialiser ────────────────────────────────────────────────────────────────


def _revision_dict(revision, *, include_diff: bool = False) -> dict:
    """Serialise a ``Revision`` to a JSON-safe dict.

    Parameters
    ----------
    include_diff:
        When True, attach the ``diff`` key (may trigger a DB read if the
        diff cache is empty).
    """
    from backend.extensions import db
    from backend.models.post import Post

    # Compute staleness: post.version has moved past the base snapshot.
    post = db.session.get(Post, revision.post_id)
    current_version = post.version if post else revision.base_version_number
    is_stale = current_version > revision.base_version_number

    d: dict = {
        "id": revision.id,
        "post_id": revision.post_id,
        "author_id": revision.author_id,
        "summary": revision.summary,
        "status": revision.status.value,
        "base_version_number": revision.base_version_number,
        "is_stale": is_stale,
        "rejection_note": revision.rejection_note,
        "reviewed_by_id": revision.reviewed_by_id,
        "reviewed_at": (
            revision.reviewed_at.isoformat() if revision.reviewed_at else None
        ),
        "created_at": revision.created_at.isoformat(),
        "updated_at": revision.updated_at.isoformat(),
    }
    if include_diff:
        try:
            d["diff"] = RevisionService.get_diff(revision.id)
        except RevisionError:
            d["diff"] = None
    return d


# ── Submit ────────────────────────────────────────────────────────────────────


@api_revisions_bp.post("/posts/<slug>/revisions")
@api_require_auth
def submit_revision(slug: str):
    """Submit a revision proposal for a published post."""
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    data, err = load_json(SubmitRevisionSchema())
    if err:
        return err
    proposed_markdown = data["proposed_markdown"]
    summary = data["summary"]

    user = get_current_user()
    try:
        revision = RevisionService.submit(
            post_id=post.id,
            author_id=user.id,
            proposed_markdown=proposed_markdown,
            summary=summary,
        )
    except RevisionError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(_revision_dict(revision)), 201


# ── List for post ─────────────────────────────────────────────────────────────


@api_revisions_bp.get("/posts/<slug>/revisions")
@api_require_role("editor", "admin")
def list_post_revisions(slug: str):
    """List all revisions for a post (editor+ only)."""
    post = PostService.get_by_slug(slug)
    if post is None:
        return jsonify({"error": "Post not found."}), 404

    status_param = request.args.get("status")
    status_filter: RevisionStatus | None = None
    if status_param:
        try:
            status_filter = RevisionStatus(status_param)
        except ValueError:
            return jsonify({"error": f"Invalid status value: {status_param!r}."}), 400

    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 100)

    revisions, total = RevisionService.list_for_post(
        post.id, page, per_page, status=status_filter
    )
    return jsonify(
        {
            "items": [_revision_dict(r) for r in revisions],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    )


# ── Pending queue ─────────────────────────────────────────────────────────────


@api_revisions_bp.get("/revisions/pending")
@api_require_role("editor", "admin")
def list_pending_revisions():
    """Return the full pending revision queue across all posts (editor+ only)."""
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 100)

    revisions, total = RevisionService.list_pending(page, per_page)
    return jsonify(
        {
            "items": [_revision_dict(r) for r in revisions],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    )


# ── Single revision ───────────────────────────────────────────────────────────


@api_revisions_bp.get("/revisions/<int:revision_id>")
@api_require_role("editor", "admin")
def get_revision(revision_id: int):
    """Get a single revision by ID (editor+ only)."""
    revision = RevisionService.get_by_id(revision_id)
    if revision is None:
        return jsonify({"error": "Revision not found."}), 404
    return jsonify(_revision_dict(revision))


# ── Diff ──────────────────────────────────────────────────────────────────────


@api_revisions_bp.get("/revisions/<int:revision_id>/diff")
@api_require_role("editor", "admin")
def get_revision_diff(revision_id: int):
    """Return the unified diff for a revision (editor+ only)."""
    try:
        diff = RevisionService.get_diff(revision_id)
    except RevisionError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"revision_id": revision_id, "diff": diff})


# ── Accept ────────────────────────────────────────────────────────────────────


@api_revisions_bp.post("/revisions/<int:revision_id>/accept")
@api_require_role("editor", "admin")
def accept_revision(revision_id: int):
    """Accept a pending revision (editor+ only)."""
    user = get_current_user()
    try:
        revision = RevisionService.accept(revision_id, reviewer_id=user.id)
    except RevisionError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify(_revision_dict(revision))


# ── Reject ────────────────────────────────────────────────────────────────────


@api_revisions_bp.post("/revisions/<int:revision_id>/reject")
@api_require_role("editor", "admin")
def reject_revision(revision_id: int):
    """Reject a pending revision with an optional note (editor+ only)."""
    user = get_current_user()
    data, err = load_json(RejectRevisionSchema())
    if err:
        return err
    note = data["note"]

    try:
        revision = RevisionService.reject(revision_id, reviewer_id=user.id, note=note)
    except RevisionError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify(_revision_dict(revision))
