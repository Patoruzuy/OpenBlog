"""Comment API endpoint tests.

Uses the ``auth_client`` and ``make_user_token`` fixtures.
All rate limits are disabled in TestingConfig.
"""

from __future__ import annotations

# ── Helpers ────────────────────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_published_post(client, token: str, title: str = "Test Article") -> str:
    """Create and immediately publish a post via the API, return its slug."""
    data = client.post(
        "/api/posts/",
        json={"title": title, "markdown_body": "Body text."},
        headers=_auth(token),
    ).get_json()
    slug = data["slug"]
    client.post(f"/api/posts/{slug}/publish", json={}, headers=_auth(token))
    return slug


def _create_comment(
    client, token: str, slug: str, *, body: str, parent_id: int | None = None
) -> dict:
    """POST a comment and assert 201; return the response body."""
    payload: dict = {"body": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    resp = client.post(
        f"/api/posts/{slug}/comments", json=payload, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


# ── GET /api/posts/<slug>/comments ────────────────────────────────────────────


class TestListComments:
    def test_returns_200_anonymous(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        slug = _make_published_post(auth_client, ed_token)
        resp = auth_client.get(f"/api/posts/{slug}/comments")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "comments" in data and "total" in data
        assert data["total"] == 0

    def test_returns_404_for_draft(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = auth_client.post(
            "/api/posts/", json={"title": "Draft Post"}, headers=_auth(token)
        ).get_json()
        resp = auth_client.get(f"/api/posts/{draft['slug']}/comments")
        assert resp.status_code == 404

    def test_threaded_structure(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        parent = _create_comment(auth_client, user_token, slug, body="Top level")
        _create_comment(
            auth_client, user_token, slug, body="Reply", parent_id=parent["id"]
        )
        data = auth_client.get(f"/api/posts/{slug}/comments").get_json()
        assert data["total"] == 1  # one top-level
        assert len(data["comments"][0]["replies"]) == 1

    def test_editor_sees_flagged_comments(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Spam")
        auth_client.post(f"/api/comments/{c['id']}/flag", headers=_auth(user_token))
        # Anonymous sees 0 (flagged hidden)
        anon = auth_client.get(f"/api/posts/{slug}/comments").get_json()
        assert anon["total"] == 0
        # Editor sees 1
        mod = auth_client.get(
            f"/api/posts/{slug}/comments", headers=_auth(ed_token)
        ).get_json()
        assert mod["total"] == 1


# ── POST /api/posts/<slug>/comments ───────────────────────────────────────────


class TestCreateComment:
    def test_anonymous_returns_401(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        slug = _make_published_post(auth_client, ed_token)
        resp = auth_client.post(f"/api/posts/{slug}/comments", json={"body": "Hi"})
        assert resp.status_code == 401

    def test_create_top_level_201(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        resp = auth_client.post(
            f"/api/posts/{slug}/comments",
            json={"body": "Great post!"},
            headers=_auth(user_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["body"] == "Great post!"
        assert data["parent_id"] is None

    def test_create_reply_201(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        parent = _create_comment(auth_client, user_token, slug, body="Top level")
        reply = _create_comment(
            auth_client, user_token, slug, body="Reply", parent_id=parent["id"]
        )
        assert reply["parent_id"] == parent["id"]

    def test_empty_body_returns_400(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        resp = auth_client.post(
            f"/api/posts/{slug}/comments",
            json={"body": "   "},
            headers=_auth(user_token),
        )
        assert resp.status_code == 400

    def test_deep_nesting_returns_400(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        parent = _create_comment(auth_client, user_token, slug, body="Top")
        reply = _create_comment(
            auth_client, user_token, slug, body="Reply", parent_id=parent["id"]
        )
        resp = auth_client.post(
            f"/api/posts/{slug}/comments",
            json={"body": "Too deep", "parent_id": reply["id"]},
            headers=_auth(user_token),
        )
        assert resp.status_code == 400


# ── PUT /api/comments/<id> ────────────────────────────────────────────────────


class TestUpdateComment:
    def test_anonymous_returns_401(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Original")
        resp = auth_client.put(f"/api/comments/{c['id']}", json={"body": "New"})
        assert resp.status_code == 401

    def test_author_can_update(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Original")
        resp = auth_client.put(
            f"/api/comments/{c['id']}",
            json={"body": "Updated"},
            headers=_auth(user_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["body"] == "Updated"

    def test_non_author_returns_403(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, author_token = make_user_token()
        _, other_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, author_token, slug, body="Mine")
        resp = auth_client.put(
            f"/api/comments/{c['id']}",
            json={"body": "Hijack"},
            headers=_auth(other_token),
        )
        assert resp.status_code == 403

    def test_deleted_comment_returns_400(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Hello")
        auth_client.delete(f"/api/comments/{c['id']}", headers=_auth(user_token))
        resp = auth_client.put(
            f"/api/comments/{c['id']}",
            json={"body": "Updated"},
            headers=_auth(user_token),
        )
        assert resp.status_code == 400


# ── DELETE /api/comments/<id> ─────────────────────────────────────────────────


class TestDeleteComment:
    def test_anonymous_returns_401(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Hello")
        resp = auth_client.delete(f"/api/comments/{c['id']}")
        assert resp.status_code == 401

    def test_author_can_delete(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Hello")
        resp = auth_client.delete(f"/api/comments/{c['id']}", headers=_auth(user_token))
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] is True

    def test_editor_can_delete_any(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Hello")
        resp = auth_client.delete(f"/api/comments/{c['id']}", headers=_auth(ed_token))
        assert resp.status_code == 200

    def test_non_author_reader_returns_403(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, author_token = make_user_token()
        _, other_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, author_token, slug, body="Mine")
        resp = auth_client.delete(
            f"/api/comments/{c['id']}", headers=_auth(other_token)
        )
        assert resp.status_code == 403


# ── POST /api/comments/<id>/flag ──────────────────────────────────────────────


class TestFlagComment:
    def test_anonymous_returns_401(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Spam")
        resp = auth_client.post(f"/api/comments/{c['id']}/flag")
        assert resp.status_code == 401

    def test_authenticated_can_flag(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Spam")
        resp = auth_client.post(
            f"/api/comments/{c['id']}/flag", headers=_auth(user_token)
        )
        assert resp.status_code == 200
        assert resp.get_json()["flagged"] is True


# ── POST /api/comments/<id>/unflag ────────────────────────────────────────────


class TestUnflagComment:
    def test_anonymous_returns_401(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Spam")
        auth_client.post(f"/api/comments/{c['id']}/flag", headers=_auth(user_token))
        resp = auth_client.post(f"/api/comments/{c['id']}/unflag")
        assert resp.status_code == 401

    def test_editor_can_unflag(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Spam")
        auth_client.post(f"/api/comments/{c['id']}/flag", headers=_auth(user_token))
        resp = auth_client.post(
            f"/api/comments/{c['id']}/unflag", headers=_auth(ed_token)
        )
        assert resp.status_code == 200
        assert resp.get_json()["flagged"] is False

    def test_reader_cannot_unflag(self, auth_client, make_user_token):
        _, ed_token = make_user_token(role="editor")
        _, user_token = make_user_token()
        slug = _make_published_post(auth_client, ed_token)
        c = _create_comment(auth_client, user_token, slug, body="Spam")
        auth_client.post(f"/api/comments/{c['id']}/flag", headers=_auth(user_token))
        resp = auth_client.post(
            f"/api/comments/{c['id']}/unflag", headers=_auth(user_token)
        )
        assert resp.status_code == 403
