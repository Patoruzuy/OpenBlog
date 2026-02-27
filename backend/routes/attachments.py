"""Attachment routes — upload, secure download, and delete.

Routes
------
POST  /api/comments/<comment_id>/attachments   upload a file
GET   /attachments/comments/<attachment_id>    download (Content-Disposition: attachment)
GET   /attachments/comments/<attachment_id>/preview  inline preview (images only)
DELETE /api/attachments/<attachment_id>        soft-delete

Security
--------
- Stored filenames are UUIDv4-based; original filename is metadata only.
- Content-Type is determined by magic-byte sniffing, not client headers.
- Downloads always set X-Content-Type-Options: nosniff.
- Draft-post attachments are only accessible to the post author/editor/admin.
- Attachment ID cannot be used to cross-access another post's comments (IDOR check).
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file
from sqlalchemy import select

from backend.extensions import csrf, db
from backend.models.comment import Comment
from backend.models.comment_attachment import CommentAttachment
from backend.models.post import Post, PostStatus
from backend.models.user import UserRole
from backend.services.media_service import MediaError, MediaService
from backend.utils.auth import api_require_auth, get_current_user

attachments_bp = Blueprint("attachments", __name__)
api_attachments_bp = Blueprint("api_attachments", __name__, url_prefix="/api")
csrf.exempt(api_attachments_bp)


# ── Upload ────────────────────────────────────────────────────────────────────

@api_attachments_bp.post("/comments/<int:comment_id>/attachments")
@api_require_auth
def upload_attachment(comment_id: int):
    """Attach a file to an existing comment.

    The comment must already exist.  The uploader must be the comment author
    or have editor/admin role.
    """
    user = get_current_user()

    comment = db.session.get(Comment, comment_id)
    if comment is None or comment.is_deleted:
        abort(404)

    # IDOR guard: ensure the comment's post exists and is accessible.
    post = db.session.get(Post, comment.post_id)
    if post is None:
        abort(404)

    # Only allow upload by the comment author, editor, or admin.
    if comment.author_id != user.id and user.role not in (UserRole.editor, UserRole.admin):
        abort(403)

    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "No file provided."}), 400

    content_length = request.content_length

    try:
        mime, ext, original_filename, is_image = MediaService.validate_upload(
            file, declared_size=content_length
        )
    except MediaError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    # Create the DB record first to get the ID (used as storage subdirectory).
    attachment = CommentAttachment(
        comment_id=comment_id,
        uploader_id=user.id,
        original_filename=original_filename,
        mime_type=mime,
        size_bytes=0,  # updated after write
        is_image=is_image,
        storage_status="pending",
    )
    db.session.add(attachment)
    db.session.flush()  # get attachment.id

    try:
        stored_path, sha256 = MediaService.store(file, attachment.id, ext)
    except OSError as exc:
        db.session.rollback()
        return jsonify({"error": "Storage error; please try again."}), 500

    # Re-read the actual size from the file that was just written.
    abs_path = MediaService.resolve_path(stored_path)
    size = abs_path.stat().st_size

    attachment.stored_path = stored_path
    attachment.sha256 = sha256
    attachment.size_bytes = size
    attachment.storage_status = "stored"
    db.session.commit()

    return jsonify({
        "id": attachment.id,
        "original_filename": attachment.original_filename,
        "mime_type": attachment.mime_type,
        "size_bytes": attachment.size_bytes,
        "is_image": attachment.is_image,
        "download_url": f"/attachments/comments/{attachment.id}",
        "preview_url": f"/attachments/comments/{attachment.id}/preview" if attachment.is_image else None,
    }), 201


# ── Download ──────────────────────────────────────────────────────────────────

def _get_accessible_attachment(attachment_id: int) -> CommentAttachment:
    """Fetch an attachment the current viewer is allowed to access.

    Public rule: the attachment's post must be published (or viewer is author/editor/admin).
    IDOR rule: returns 404 (not 403) for unknown IDs.
    """
    attachment = db.session.get(CommentAttachment, attachment_id)
    if attachment is None or attachment.storage_status == "deleted":
        abort(404)

    comment = db.session.get(Comment, attachment.comment_id)
    if comment is None or comment.is_deleted:
        abort(404)

    post = db.session.get(Post, comment.post_id)
    if post is None:
        abort(404)

    if post.status != PostStatus.published:
        # Non-published: only author / editor / admin may access.
        viewer = get_current_user()
        if viewer is None:
            abort(404)  # use 404 to avoid leaking draft existence
        if viewer.id != post.author_id and viewer.role not in (UserRole.editor, UserRole.admin):
            abort(404)

    if attachment.stored_path is None:
        abort(404)

    return attachment


@attachments_bp.get("/attachments/comments/<int:attachment_id>")
def download_attachment(attachment_id: int):
    """Serve the file as a download with safe headers."""
    attachment = _get_accessible_attachment(attachment_id)
    abs_path = MediaService.resolve_path(attachment.stored_path)  # type: ignore[arg-type]

    if not abs_path.exists():
        abort(404)

    response = send_file(
        abs_path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=attachment.original_filename,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@attachments_bp.get("/attachments/comments/<int:attachment_id>/preview")
def preview_attachment(attachment_id: int):
    """Serve safe images inline for preview; non-images redirect to download."""
    attachment = _get_accessible_attachment(attachment_id)

    if not attachment.is_image:
        # Redirect to regular download for non-image types.
        from flask import redirect, url_for  # noqa: PLC0415
        return redirect(url_for("attachments.download_attachment", attachment_id=attachment_id))

    abs_path = MediaService.resolve_path(attachment.stored_path)  # type: ignore[arg-type]
    if not abs_path.exists():
        abort(404)

    response = send_file(
        abs_path,
        mimetype=attachment.mime_type,
        as_attachment=False,
        download_name=attachment.original_filename,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


# ── Delete ────────────────────────────────────────────────────────────────────

@api_attachments_bp.delete("/attachments/<int:attachment_id>")
@api_require_auth
def delete_attachment(attachment_id: int):
    """Soft-delete an attachment (sets storage_status='deleted', removes file)."""
    user = get_current_user()
    attachment = db.session.get(CommentAttachment, attachment_id)

    if attachment is None or attachment.storage_status == "deleted":
        abort(404)

    comment = db.session.get(Comment, attachment.comment_id)
    if comment is None:
        abort(404)

    # Only the uploader, editor, or admin may delete.
    if attachment.uploader_id != user.id and user.role not in (UserRole.editor, UserRole.admin):
        abort(403)

    from datetime import UTC, datetime  # noqa: PLC0415

    attachment.storage_status = "deleted"
    attachment.deleted_at = datetime.now(UTC)
    db.session.commit()

    # Remove the physical file (best-effort).
    if attachment.stored_path:
        MediaService.delete_file(attachment.stored_path)

    return "", 204
