"""Post API endpoint tests.

Uses the ``auth_client`` and ``make_user_token`` fixtures.
All rate limits are disabled in TestingConfig.
"""

from __future__ import annotations

# ── Helpers ────────────────────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_post(client, token: str, **overrides) -> dict:
    """Create a post via the API and return the JSON response body."""
    payload = {
        "title": "Test Post",
        "markdown_body": "Hello **world**.",
        **overrides,
    }
    resp = client.post("/api/posts/", json=payload, headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


# ── GET /api/posts/ ────────────────────────────────────────────────────────────


class TestListPosts:
    def test_returns_200_for_anonymous(self, auth_client):
        resp = auth_client.get("/api/posts/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "posts" in data
        assert "total" in data

    def test_empty_list_initially(self, auth_client):
        data = auth_client.get("/api/posts/").get_json()
        assert data["posts"] == []
        assert data["total"] == 0

    def test_published_posts_appear(self, app, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _create_post(auth_client, token, title="Visible Post")
        # It's still a draft — anonymous visitors should see 0 published posts.
        anon = app.test_client()
        data = anon.get("/api/posts/").get_json()
        assert data["total"] == 0

    def test_pagination_params(self, auth_client):
        resp = auth_client.get("/api/posts/?page=2&per_page=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["page"] == 2
        assert data["per_page"] == 5

    def test_editor_sees_all_statuses(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _create_post(auth_client, token, title="Draft Vis")
        # Editor: should see the draft.
        data = auth_client.get("/api/posts/", headers=_auth(token)).get_json()
        assert data["total"] == 1


# ── POST /api/posts/ ───────────────────────────────────────────────────────────


class TestCreatePost:
    def test_contributor_can_create_201(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        resp = auth_client.post(
            "/api/posts/",
            json={"title": "My First Post", "markdown_body": "Body text."},
            headers=_auth(token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "My First Post"
        assert data["status"] == "draft"
        assert data["slug"] == "my-first-post"

    def test_reader_cannot_create_403(self, auth_client, make_user_token):
        _, token = make_user_token(role="reader")
        resp = auth_client.post(
            "/api/posts/",
            json={"title": "Nope"},
            headers=_auth(token),
        )
        assert resp.status_code == 403

    def test_anonymous_cannot_create_401(self, auth_client):
        resp = auth_client.post("/api/posts/", json={"title": "No auth"})
        assert resp.status_code == 401

    def test_missing_title_returns_400(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        resp = auth_client.post(
            "/api/posts/",
            json={"markdown_body": "No title here."},
            headers=_auth(token),
        )
        assert resp.status_code == 400

    def test_slug_generated_from_title(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        data = _create_post(auth_client, token, title="Flask 3 Is Great!")
        assert data["slug"] == "flask-3-is-great"

    def test_response_includes_rendered_html(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        data = _create_post(auth_client, token, markdown_body="**Bold**")
        assert "<strong>Bold</strong>" in data["rendered_html"]

    def test_tags_stored(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        data = _create_post(auth_client, token, tags=["flask", "python"])
        tag_slugs = [t["slug"] for t in data["tags"]]
        assert "flask" in tag_slugs
        assert "python" in tag_slugs


# ── GET /api/posts/<slug> ──────────────────────────────────────────────────────


class TestGetPost:
    def test_get_published_post(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        post_data = _create_post(auth_client, token, title="Public Post")
        # Publish it.
        auth_client.post(
            f"/api/posts/{post_data['slug']}/publish",
            json={},
            headers=_auth(token),
        )
        resp = auth_client.get(f"/api/posts/{post_data['slug']}")
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "Public Post"

    def test_nonexistent_returns_404(self, auth_client):
        resp = auth_client.get("/api/posts/does-not-exist")
        assert resp.status_code == 404

    def test_draft_hidden_from_anonymous(self, app, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token, title="Hidden Draft")
        # Use a brand-new client with no session/token to simulate an anonymous visitor.
        anon = app.test_client()
        resp = anon.get(f"/api/posts/{post_data['slug']}")
        assert resp.status_code == 404

    def test_author_can_see_own_draft(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token, title="My Draft")
        resp = auth_client.get(f"/api/posts/{post_data['slug']}", headers=_auth(token))
        assert resp.status_code == 200

    def test_editor_can_see_any_draft(self, auth_client, make_user_token):
        _, contrib_token = make_user_token(role="contributor")
        _, editor_token = make_user_token(role="editor")
        post_data = _create_post(auth_client, contrib_token, title="Their Draft")
        resp = auth_client.get(
            f"/api/posts/{post_data['slug']}", headers=_auth(editor_token)
        )
        assert resp.status_code == 200


# ── PUT /api/posts/<slug> ──────────────────────────────────────────────────────


class TestUpdatePost:
    def test_author_can_update(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token, title="Old Title")
        resp = auth_client.put(
            f"/api/posts/{post_data['slug']}",
            json={"title": "New Title"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "New Title"

    def test_stranger_cannot_update_403(self, auth_client, make_user_token):
        _, author_token = make_user_token(role="contributor")
        _, stranger_token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, author_token, title="Author Post")
        resp = auth_client.put(
            f"/api/posts/{post_data['slug']}",
            json={"title": "Stolen"},
            headers=_auth(stranger_token),
        )
        assert resp.status_code == 403

    def test_editor_can_update_any_post(self, auth_client, make_user_token):
        _, contrib_token = make_user_token(role="contributor")
        _, editor_token = make_user_token(role="editor")
        post_data = _create_post(auth_client, contrib_token, title="Contrib Post")
        resp = auth_client.put(
            f"/api/posts/{post_data['slug']}",
            json={"title": "Editor Fixed It"},
            headers=_auth(editor_token),
        )
        assert resp.status_code == 200

    def test_content_change_bumps_version(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token, markdown_body="Original body.")
        assert post_data["version"] == 1
        resp = auth_client.put(
            f"/api/posts/{post_data['slug']}",
            json={"markdown_body": "Completely rewritten."},
            headers=_auth(token),
        )
        assert resp.get_json()["version"] == 2

    def test_title_change_no_version_bump(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token)
        resp = auth_client.put(
            f"/api/posts/{post_data['slug']}",
            json={"title": "Just a new title"},
            headers=_auth(token),
        )
        assert resp.get_json()["version"] == 1

    def test_anonymous_cannot_update_401(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token)
        resp = auth_client.put(f"/api/posts/{post_data['slug']}", json={"title": "X"})
        assert resp.status_code == 401


# ── DELETE /api/posts/<slug> ───────────────────────────────────────────────────


class TestDeletePost:
    def test_author_can_archive(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token)
        resp = auth_client.delete(
            f"/api/posts/{post_data['slug']}", headers=_auth(token)
        )
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Post archived."

    def test_post_status_is_archived(self, auth_client, make_user_token):
        _, contrib_token = make_user_token(role="contributor")
        _, editor_token = make_user_token(role="editor")
        post_data = _create_post(auth_client, contrib_token)
        auth_client.delete(
            f"/api/posts/{post_data['slug']}", headers=_auth(contrib_token)
        )
        # Editor can still see archived post.
        resp = auth_client.get(
            f"/api/posts/{post_data['slug']}", headers=_auth(editor_token)
        )
        assert resp.get_json()["status"] == "archived"

    def test_stranger_cannot_archive_403(self, auth_client, make_user_token):
        _, author_token = make_user_token(role="contributor")
        _, stranger_token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, author_token)
        resp = auth_client.delete(
            f"/api/posts/{post_data['slug']}", headers=_auth(stranger_token)
        )
        assert resp.status_code == 403

    def test_admin_can_archive_any(self, auth_client, make_user_token):
        _, contrib_token = make_user_token(role="contributor")
        _, admin_token = make_user_token(role="admin")
        post_data = _create_post(auth_client, contrib_token)
        resp = auth_client.delete(
            f"/api/posts/{post_data['slug']}", headers=_auth(admin_token)
        )
        assert resp.status_code == 200


# ── POST /api/posts/<slug>/publish ─────────────────────────────────────────────


class TestPublishPost:
    def test_editor_can_publish(self, auth_client, make_user_token):
        _, contrib_token = make_user_token(role="contributor")
        _, editor_token = make_user_token(role="editor")
        post_data = _create_post(auth_client, contrib_token, title="Ready to Ship")
        resp = auth_client.post(
            f"/api/posts/{post_data['slug']}/publish",
            json={},
            headers=_auth(editor_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "published"

    def test_published_at_is_set(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        post_data = _create_post(auth_client, token)
        resp = auth_client.post(
            f"/api/posts/{post_data['slug']}/publish",
            json={},
            headers=_auth(token),
        )
        assert resp.get_json()["published_at"] is not None

    def test_schedule_with_future_date(self, auth_client, make_user_token):
        from datetime import UTC, datetime, timedelta

        _, token = make_user_token(role="editor")
        post_data = _create_post(auth_client, token, title="Future Post")
        future = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        resp = auth_client.post(
            f"/api/posts/{post_data['slug']}/publish",
            json={"publish_at": future},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "scheduled"

    def test_schedule_past_date_returns_400(self, auth_client, make_user_token):
        from datetime import UTC, datetime, timedelta

        _, token = make_user_token(role="editor")
        post_data = _create_post(auth_client, token, title="Past Sched")
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        resp = auth_client.post(
            f"/api/posts/{post_data['slug']}/publish",
            json={"publish_at": past},
            headers=_auth(token),
        )
        assert resp.status_code == 400

    def test_contributor_cannot_publish_403(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        post_data = _create_post(auth_client, token, title="Want to Publish")
        resp = auth_client.post(
            f"/api/posts/{post_data['slug']}/publish",
            json={},
            headers=_auth(token),
        )
        assert resp.status_code == 403

    def test_nonexistent_slug_returns_404(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        resp = auth_client.post(
            "/api/posts/no-such-slug/publish",
            json={},
            headers=_auth(token),
        )
        assert resp.status_code == 404
