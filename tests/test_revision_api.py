"""Tests for the revisions API endpoints."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.services.revision_service import RevisionService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    user, tok = make_user_token("author@example.com", "postauthor")
    return user, tok


@pytest.fixture()
def contributor(make_user_token):
    user, tok = make_user_token("contrib@example.com", "contrib", role="contributor")
    return user, tok


@pytest.fixture()
def editor(make_user_token):
    user, tok = make_user_token("editor@example.com", "editor", role="editor")
    return user, tok


@pytest.fixture()
def reader(make_user_token):
    user, tok = make_user_token("reader@example.com", "reader")
    return user, tok


@pytest.fixture()
def pub_post(author):
    user, _ = author
    post = Post(
        author_id=user.id,
        title="Revisable Post",
        slug="revisable-post",
        markdown_body="# Original\n\nOriginal content here.",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


@pytest.fixture()
def pending_revision(contributor, pub_post):
    user, _ = contributor
    return RevisionService.submit(
        post_id=pub_post.id,
        author_id=user.id,
        proposed_markdown="# Original\n\nImproved content here.",
        summary="Improve wording",
    )


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── POST /api/posts/<slug>/revisions ─────────────────────────────────────────


class TestSubmitRevision:
    def test_submit_returns_201(self, auth_client, contributor, pub_post):
        _, tok = contributor
        resp = auth_client.post(
            f"/api/posts/{pub_post.slug}/revisions",
            json={
                "proposed_markdown": "# Original\n\nEnhanced content.",
                "summary": "Enhance the content",
            },
            headers=_h(tok),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "pending"
        assert data["summary"] == "Enhance the content"
        assert "id" in data

    def test_submit_requires_auth(self, auth_client, pub_post):
        resp = auth_client.post(
            f"/api/posts/{pub_post.slug}/revisions",
            json={"proposed_markdown": "# New", "summary": "Change"},
        )
        assert resp.status_code == 401

    def test_submit_unknown_post_404(self, auth_client, contributor):
        _, tok = contributor
        resp = auth_client.post(
            "/api/posts/no-such-post/revisions",
            json={"proposed_markdown": "# New", "summary": "Change"},
            headers=_h(tok),
        )
        assert resp.status_code == 404

    def test_submit_missing_proposed_markdown_400(self, auth_client, contributor, pub_post):
        _, tok = contributor
        resp = auth_client.post(
            f"/api/posts/{pub_post.slug}/revisions",
            json={"summary": "Change"},
            headers=_h(tok),
        )
        assert resp.status_code == 400

    def test_author_cannot_submit_own_post_400(self, auth_client, author, pub_post):
        _, tok = author
        resp = auth_client.post(
            f"/api/posts/{pub_post.slug}/revisions",
            json={
                "proposed_markdown": "# Original\n\nAuthor's own change.",
                "summary": "Author edit",
            },
            headers=_h(tok),
        )
        assert resp.status_code == 400

    def test_identical_markdown_400(self, auth_client, contributor, pub_post):
        _, tok = contributor
        resp = auth_client.post(
            f"/api/posts/{pub_post.slug}/revisions",
            json={
                "proposed_markdown": pub_post.markdown_body,
                "summary": "No change",
            },
            headers=_h(tok),
        )
        assert resp.status_code == 400

    def test_blank_summary_400(self, auth_client, contributor, pub_post):
        _, tok = contributor
        resp = auth_client.post(
            f"/api/posts/{pub_post.slug}/revisions",
            json={
                "proposed_markdown": "# Original\n\nNew content.",
                "summary": "",
            },
            headers=_h(tok),
        )
        assert resp.status_code == 400


# ── GET /api/posts/<slug>/revisions ──────────────────────────────────────────


class TestListPostRevisions:
    def test_editor_sees_revisions(
        self, auth_client, editor, pending_revision, pub_post
    ):
        _, tok = editor
        resp = auth_client.get(
            f"/api/posts/{pub_post.slug}/revisions", headers=_h(tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == pending_revision.id

    def test_reader_gets_403(self, auth_client, reader, pub_post):
        _, tok = reader
        resp = auth_client.get(
            f"/api/posts/{pub_post.slug}/revisions", headers=_h(tok)
        )
        assert resp.status_code == 403

    def test_status_filter(self, auth_client, editor, pending_revision, pub_post):
        _, tok = editor
        resp = auth_client.get(
            f"/api/posts/{pub_post.slug}/revisions?status=accepted",
            headers=_h(tok),
        )
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 0

    def test_invalid_status_400(self, auth_client, editor, pub_post):
        _, tok = editor
        resp = auth_client.get(
            f"/api/posts/{pub_post.slug}/revisions?status=bogus",
            headers=_h(tok),
        )
        assert resp.status_code == 400

    def test_unauthenticated_gets_401(self, auth_client, pub_post):
        resp = auth_client.get(f"/api/posts/{pub_post.slug}/revisions")
        assert resp.status_code == 401


# ── GET /api/revisions/pending ────────────────────────────────────────────────


class TestListPendingRevisions:
    def test_returns_pending_queue(self, auth_client, editor, pending_revision):
        _, tok = editor
        resp = auth_client.get("/api/revisions/pending", headers=_h(tok))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        assert all(item["status"] == "pending" for item in data["items"])

    def test_reader_gets_403(self, auth_client, reader, pending_revision):
        _, tok = reader
        resp = auth_client.get("/api/revisions/pending", headers=_h(tok))
        assert resp.status_code == 403

    def test_contributor_gets_403(self, auth_client, contributor, pending_revision):
        _, tok = contributor
        resp = auth_client.get("/api/revisions/pending", headers=_h(tok))
        assert resp.status_code == 403


# ── GET /api/revisions/<id> ───────────────────────────────────────────────────


class TestGetRevision:
    def test_editor_can_get_revision(self, auth_client, editor, pending_revision):
        _, tok = editor
        resp = auth_client.get(
            f"/api/revisions/{pending_revision.id}", headers=_h(tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == pending_revision.id
        assert data["status"] == "pending"

    def test_not_found_returns_404(self, auth_client, editor):
        _, tok = editor
        resp = auth_client.get("/api/revisions/99999", headers=_h(tok))
        assert resp.status_code == 404

    def test_reader_gets_403(self, auth_client, reader, pending_revision):
        _, tok = reader
        resp = auth_client.get(
            f"/api/revisions/{pending_revision.id}", headers=_h(tok)
        )
        assert resp.status_code == 403

    def test_stale_flag_false_when_fresh(self, auth_client, editor, pending_revision):
        _, tok = editor
        resp = auth_client.get(
            f"/api/revisions/{pending_revision.id}", headers=_h(tok)
        )
        assert resp.get_json()["is_stale"] is False


# ── GET /api/revisions/<id>/diff ─────────────────────────────────────────────


class TestGetRevisionDiff:
    def test_returns_diff_string(self, auth_client, editor, pending_revision):
        _, tok = editor
        resp = auth_client.get(
            f"/api/revisions/{pending_revision.id}/diff", headers=_h(tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "diff" in data
        assert "---" in data["diff"]

    def test_not_found_returns_404(self, auth_client, editor):
        _, tok = editor
        resp = auth_client.get("/api/revisions/99999/diff", headers=_h(tok))
        assert resp.status_code == 404

    def test_reader_gets_403(self, auth_client, reader, pending_revision):
        _, tok = reader
        resp = auth_client.get(
            f"/api/revisions/{pending_revision.id}/diff", headers=_h(tok)
        )
        assert resp.status_code == 403


# ── POST /api/revisions/<id>/accept ──────────────────────────────────────────


class TestAcceptRevision:
    def test_accept_returns_200_accepted(
        self, auth_client, editor, pending_revision, pub_post
    ):
        _, tok = editor
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/accept", headers=_h(tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "accepted"

    def test_accept_updates_post_body(
        self, auth_client, editor, pending_revision, pub_post
    ):
        _, tok = editor
        proposed = pending_revision.proposed_markdown
        auth_client.post(
            f"/api/revisions/{pending_revision.id}/accept", headers=_h(tok)
        )
        db.session.expire(pub_post)
        assert pub_post.markdown_body == proposed

    def test_accept_bumps_post_version(
        self, auth_client, editor, pending_revision, pub_post
    ):
        _, tok = editor
        original_version = pub_post.version
        auth_client.post(
            f"/api/revisions/{pending_revision.id}/accept", headers=_h(tok)
        )
        db.session.expire(pub_post)
        assert pub_post.version == original_version + 1

    def test_accept_already_accepted_400(
        self, auth_client, editor, pending_revision
    ):
        _, tok = editor
        auth_client.post(
            f"/api/revisions/{pending_revision.id}/accept", headers=_h(tok)
        )
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/accept", headers=_h(tok)
        )
        assert resp.status_code == 400

    def test_not_found_returns_404(self, auth_client, editor):
        _, tok = editor
        resp = auth_client.post("/api/revisions/99999/accept", headers=_h(tok))
        assert resp.status_code == 404

    def test_reader_gets_403(self, auth_client, reader, pending_revision):
        _, tok = reader
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/accept", headers=_h(tok)
        )
        assert resp.status_code == 403

    def test_contributor_gets_403(self, auth_client, contributor, pending_revision):
        _, tok = contributor
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/accept", headers=_h(tok)
        )
        assert resp.status_code == 403


# ── POST /api/revisions/<id>/reject ──────────────────────────────────────────


class TestRejectRevision:
    def test_reject_returns_200_rejected(
        self, auth_client, editor, pending_revision
    ):
        _, tok = editor
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/reject",
            json={"note": "Needs more detail."},
            headers=_h(tok),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "rejected"
        assert data["rejection_note"] == "Needs more detail."

    def test_reject_without_note(self, auth_client, editor, pending_revision):
        _, tok = editor
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/reject",
            json={},
            headers=_h(tok),
        )
        assert resp.status_code == 200
        assert resp.get_json()["rejection_note"] is None

    def test_reject_already_rejected_400(
        self, auth_client, editor, pending_revision
    ):
        _, tok = editor
        auth_client.post(
            f"/api/revisions/{pending_revision.id}/reject", headers=_h(tok)
        )
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/reject", headers=_h(tok)
        )
        assert resp.status_code == 400

    def test_not_found_returns_404(self, auth_client, editor):
        _, tok = editor
        resp = auth_client.post("/api/revisions/99999/reject", headers=_h(tok))
        assert resp.status_code == 404

    def test_reader_gets_403(self, auth_client, reader, pending_revision):
        _, tok = reader
        resp = auth_client.post(
            f"/api/revisions/{pending_revision.id}/reject", headers=_h(tok)
        )
        assert resp.status_code == 403

    def test_post_body_unchanged_after_reject(
        self, auth_client, editor, pending_revision, pub_post
    ):
        _, tok = editor
        original_body = pub_post.markdown_body
        auth_client.post(
            f"/api/revisions/{pending_revision.id}/reject",
            json={"note": "Rejected."},
            headers=_h(tok),
        )
        db.session.expire(pub_post)
        assert pub_post.markdown_body == original_body
