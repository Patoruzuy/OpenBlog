"""Tests for comment attachment upload / download / delete.

Security scenarios covered
--------------------------
- Disallowed extension is rejected (415)
- Oversize file is rejected (413)
- Upload stores file outside backend/static
- Stored filename is a server-generated UUID, not the client filename
- Path-traversal filename is sanitised
- Download sets X-Content-Type-Options: nosniff
- Download serves file with Content-Disposition: attachment
- Preview route uses Content-Disposition: inline for images
- Preview route redirects to download for non-image attachments
- Draft-post attachments return 404 to anonymous viewers
- IDOR: unrelated user cannot access another user's draft attachment
- Delete soft-deletes; second download returns 404
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from backend.extensions import db as _db
from backend.models.comment import Comment
from backend.models.comment_attachment import CommentAttachment
from backend.models.post import PostStatus
from backend.services.auth_service import AuthService

# ── PNG magic bytes (minimal 1×1 transparent PNG) ─────────────────────────────
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_published_post(client, token: str, title: str = "Article") -> dict:
    data = client.post(
        "/api/posts/",
        json={"title": title, "markdown_body": "Body text."},
        headers=_auth(token),
    ).get_json()
    client.post(f"/api/posts/{data['slug']}/publish", json={}, headers=_auth(token))
    return data


def _make_comment(client, token: str, slug: str, body: str = "Nice post!") -> dict:
    resp = client.post(
        f"/api/posts/{slug}/comments",
        json={"body": body},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _upload(client, token: str, comment_id: int, *, filename: str, data: bytes,
            content_type: str = "image/png") -> "flask.testing.FlaskClient":
    return client.post(
        f"/api/comments/{comment_id}/attachments",
        data={"file": (io.BytesIO(data), filename, content_type)},
        content_type="multipart/form-data",
        headers=_auth(token),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def media_dir(app, tmp_path):
    """Override MEDIA_ROOT to a temporary directory for each test."""
    original = app.config.get("MEDIA_ROOT")
    app.config["MEDIA_ROOT"] = str(tmp_path / "media")
    yield tmp_path / "media"
    app.config["MEDIA_ROOT"] = original


# ── Upload validation ─────────────────────────────────────────────────────────


class TestUploadValidation:
    def test_rejects_disallowed_extension(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        resp = _upload(auth_client, token, comment["id"],
                       filename="malware.exe", data=b"MZ\x00\x00")
        assert resp.status_code == 415

    def test_rejects_svg_extension(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
        resp = _upload(auth_client, token, comment["id"],
                       filename="xss.svg", data=svg, content_type="image/svg+xml")
        assert resp.status_code == 415

    def test_rejects_empty_file(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        resp = _upload(auth_client, token, comment["id"],
                       filename="empty.png", data=b"")
        assert resp.status_code == 400

    def test_rejects_oversize_via_content_length(self, auth_client, make_user_token, media_dir, app):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        # Temporarily lower the limit so we don't need to send 5 MiB.
        original = app.config.get("MAX_COMMENT_ATTACHMENT_BYTES")
        app.config["MAX_COMMENT_ATTACHMENT_BYTES"] = 10

        try:
            resp = auth_client.post(
                f"/api/comments/{comment['id']}/attachments",
                data={"file": (io.BytesIO(_PNG_1X1), "big.png", "image/png")},
                content_type="multipart/form-data",
                headers={**_auth(token), "Content-Length": "999999"},
            )
            assert resp.status_code in (400, 413)
        finally:
            app.config["MAX_COMMENT_ATTACHMENT_BYTES"] = original

    def test_requires_auth(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        resp = auth_client.post(
            f"/api/comments/{comment['id']}/attachments",
            data={"file": (io.BytesIO(_PNG_1X1), "file.png", "image/png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 401

    def test_stranger_cannot_upload_to_others_comment(self, auth_client, make_user_token, media_dir):
        _, owner_token = make_user_token(role="editor")
        _, stranger_token = make_user_token()
        post = _make_published_post(auth_client, owner_token)
        comment = _make_comment(auth_client, owner_token, post["slug"])

        resp = _upload(auth_client, stranger_token, comment["id"],
                       filename="file.png", data=_PNG_1X1)
        assert resp.status_code == 403


# ── Upload storage security ───────────────────────────────────────────────────


class TestUploadStorage:
    def test_stores_outside_static(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        resp = _upload(auth_client, token, comment["id"],
                       filename="photo.png", data=_PNG_1X1)
        assert resp.status_code == 201

        data = resp.get_json()
        stored_path: str = data.get("download_url", "")
        # stored_path is a URL like /attachments/comments/1
        # The actual file must not be under backend/static
        attachment_id = data["id"]
        attachment = _db.session.get(CommentAttachment, attachment_id)
        assert attachment is not None
        assert "static" not in (attachment.stored_path or "").replace("\\", "/")

    def test_filename_is_server_generated_uuid(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        resp = _upload(auth_client, token, comment["id"],
                       filename="my-photo.png", data=_PNG_1X1)
        assert resp.status_code == 201

        attachment_id = resp.get_json()["id"]
        attachment = _db.session.get(CommentAttachment, attachment_id)
        stored_name = Path(attachment.stored_path).name
        # Should not contain "my-photo"
        assert "my-photo" not in stored_name
        # Should be a UUID hex + extension
        assert stored_name.endswith(".png")
        assert len(stored_name) > 10  # uuid4 hex is 32 chars + ext

    def test_path_traversal_filename_is_sanitised(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        # Attempt a path-traversal filename
        resp = _upload(auth_client, token, comment["id"],
                       filename="../../etc/passwd.png", data=_PNG_1X1)
        assert resp.status_code == 201

        attachment_id = resp.get_json()["id"]
        attachment = _db.session.get(CommentAttachment, attachment_id)
        # Stored path must not leave media root (no ".." segments)
        assert ".." not in (attachment.stored_path or "")
        # Original filename must be sanitised to just the basename
        assert "etc" not in attachment.original_filename

    def test_sha256_is_stored(self, auth_client, make_user_token, media_dir):
        import hashlib

        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        resp = _upload(auth_client, token, comment["id"],
                       filename="a.png", data=_PNG_1X1)
        assert resp.status_code == 201

        attachment_id = resp.get_json()["id"]
        attachment = _db.session.get(CommentAttachment, attachment_id)
        expected = hashlib.sha256(_PNG_1X1).hexdigest()
        assert attachment.sha256 == expected

    def test_upload_returns_download_and_preview_urls(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        resp = _upload(auth_client, token, comment["id"],
                       filename="photo.png", data=_PNG_1X1)
        assert resp.status_code == 201

        data = resp.get_json()
        assert data["download_url"].startswith("/attachments/comments/")
        assert data["preview_url"] is not None
        assert data["preview_url"].endswith("/preview")
        assert data["is_image"] is True


# ── Download security headers ─────────────────────────────────────────────────


class TestDownloadHeaders:
    def _upload_png(self, client, token, media_dir):
        post = _make_published_post(client, token)
        comment = _make_comment(client, token, post["slug"])
        resp = _upload(client, token, comment["id"], filename="img.png", data=_PNG_1X1)
        assert resp.status_code == 201
        return resp.get_json()

    def test_nosniff_header_on_download(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        uploaded = self._upload_png(auth_client, token, media_dir)

        resp = auth_client.get(uploaded["download_url"])
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_attachment_disposition_on_download(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        uploaded = self._upload_png(auth_client, token, media_dir)

        resp = auth_client.get(uploaded["download_url"])
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "").lower()

    def test_inline_disposition_on_preview(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        uploaded = self._upload_png(auth_client, token, media_dir)

        resp = auth_client.get(uploaded["preview_url"])
        assert resp.status_code == 200
        assert "inline" in resp.headers.get("Content-Disposition", "").lower()

    def test_nosniff_header_on_preview(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        uploaded = self._upload_png(auth_client, token, media_dir)

        resp = auth_client.get(uploaded["preview_url"])
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_non_image_preview_redirects_to_download(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])

        # Upload a PDF
        pdf_data = b"%PDF-1.4 minimal"
        resp = _upload(auth_client, token, comment["id"],
                       filename="doc.pdf", data=pdf_data, content_type="application/pdf")
        assert resp.status_code == 201, resp.get_json()
        attach_id = resp.get_json()["id"]

        # Preview of non-image should redirect
        resp2 = auth_client.get(f"/attachments/comments/{attach_id}/preview",
                                follow_redirects=False)
        assert resp2.status_code == 302
        assert f"/attachments/comments/{attach_id}" in resp2.headers.get("Location", "")


# ── Access control (draft posts + IDOR) ───────────────────────────────────────


class TestAttachmentAccessControl:
    def _upload_to_draft(self, auth_client, make_user_token, media_dir):
        """Create a draft post with a comment and attach a file; return (attachment_id, author_token)."""
        _, token = make_user_token(role="editor")

        # Create post but do NOT publish it
        draft_data = auth_client.post(
            "/api/posts/",
            json={"title": "Draft Article", "markdown_body": "Draft body."},
            headers=_auth(token),
        ).get_json()
        slug = draft_data["slug"]

        # We need to add a comment via the service layer since the API rejects comments on drafts
        from backend.models.post import Post
        from backend.services.comment_service import CommentService
        from backend.services.auth_service import AuthService

        # Get the post id from slug
        from sqlalchemy import select
        post = _db.session.scalar(select(Post).where(Post.slug == slug))
        _, author_token = make_user_token(role="editor")
        from backend.models.user import User
        # Get the user for the author token
        author_id = post.author_id

        comment = CommentService.create(post.id, author_id, "Draft comment")
        _db.session.commit()

        resp = _upload(auth_client, token, comment.id,
                       filename="img.png", data=_PNG_1X1)
        return resp.get_json()["id"], token

    def test_draft_attachment_hidden_from_anon(self, auth_client, make_user_token, media_dir):
        attach_id, _ = self._upload_to_draft(auth_client, make_user_token, media_dir)

        resp = auth_client.get(f"/attachments/comments/{attach_id}")
        # Anonymous visitors must not see draft attachments
        assert resp.status_code == 404

    def test_draft_attachment_hidden_from_stranger(self, auth_client, make_user_token, media_dir):
        attach_id, _ = self._upload_to_draft(auth_client, make_user_token, media_dir)
        _, stranger_token = make_user_token()

        resp = auth_client.get(f"/attachments/comments/{attach_id}",
                               headers=_auth(stranger_token))
        assert resp.status_code == 404

    def test_unknown_attachment_returns_404(self, auth_client, make_user_token, media_dir):
        resp = auth_client.get("/attachments/comments/999999")
        assert resp.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


class TestAttachmentDelete:
    def _upload_png(self, auth_client, token):
        post = _make_published_post(auth_client, token)
        comment = _make_comment(auth_client, token, post["slug"])
        resp = _upload(auth_client, token, comment["id"], filename="del.png", data=_PNG_1X1)
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_uploader_can_delete(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        attach_id = self._upload_png(auth_client, token)

        resp = auth_client.delete(f"/api/attachments/{attach_id}", headers=_auth(token))
        assert resp.status_code == 204

    def test_deleted_attachment_returns_404(self, auth_client, make_user_token, media_dir):
        _, token = make_user_token(role="editor")
        attach_id = self._upload_png(auth_client, token)

        auth_client.delete(f"/api/attachments/{attach_id}", headers=_auth(token))

        resp = auth_client.get(f"/attachments/comments/{attach_id}")
        assert resp.status_code == 404

    def test_stranger_cannot_delete(self, auth_client, make_user_token, media_dir):
        _, owner_token = make_user_token(role="editor")
        _, stranger_token = make_user_token()
        attach_id = self._upload_png(auth_client, owner_token)

        resp = auth_client.delete(f"/api/attachments/{attach_id}",
                                  headers=_auth(stranger_token))
        assert resp.status_code == 403

    def test_editor_can_delete_others_attachment(self, auth_client, make_user_token, media_dir):
        _, owner_token = make_user_token(role="editor")
        _, editor_token = make_user_token(role="editor")
        attach_id = self._upload_png(auth_client, owner_token)

        resp = auth_client.delete(f"/api/attachments/{attach_id}",
                                  headers=_auth(editor_token))
        assert resp.status_code == 204
