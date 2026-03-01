"""Tests for the POST /api/posts/<slug>/autosave endpoint.

Covers:
  - 200 happy path: body/title updated, revision incremented, timestamps set
  - 401 when unauthenticated
  - 403 when caller is not the author (or an editor)
  - 409 when client_revision is stale
  - 422 when post is not a draft (published / archived)
  - No version bump on autosave (version field is for publish milestones)
  - Partial updates: omitted fields are left unchanged
  - Tags update when provided
"""

from __future__ import annotations

# ── Helpers ────────────────────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_draft(client, token: str, **overrides) -> dict:
    """Create a draft post via the API and return the JSON body."""
    payload = {
        "title": "Initial Title",
        "markdown_body": "Initial body.",
        **overrides,
    }
    resp = client.post("/api/posts/", json=payload, headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _autosave(client, token: str, slug: str, **fields) -> tuple[int, dict]:
    """Call the autosave endpoint and return (status_code, json_body)."""
    payload = {"autosave_revision": 0, **fields}
    resp = client.post(
        f"/api/posts/{slug}/autosave", json=payload, headers=_auth(token)
    )
    return resp.status_code, resp.get_json()


# ── 200 happy path ─────────────────────────────────────────────────────────────


class TestAutosaveHappyPath:
    def test_returns_ok_true(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        status, data = _autosave(auth_client, token, draft["slug"], title="New Title")
        assert status == 200
        assert data["ok"] is True

    def test_updates_title(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token, title="Old Title")
        _autosave(auth_client, token, draft["slug"], title="New Title")
        resp = auth_client.get(f"/api/posts/{draft['slug']}", headers=_auth(token))
        assert resp.get_json()["title"] == "New Title"

    def test_updates_markdown_body(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token, markdown_body="Old body.")
        _autosave(auth_client, token, draft["slug"], markdown_body="Updated body.")
        resp = auth_client.get(f"/api/posts/{draft['slug']}", headers=_auth(token))
        assert resp.get_json()["markdown_body"] == "Updated body."

    def test_increments_autosave_revision(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        _, data = _autosave(auth_client, token, draft["slug"])
        first_rev = data["autosave_revision"]
        # Second autosave with the updated revision
        status2, data2 = _autosave(
            auth_client, token, draft["slug"], autosave_revision=first_rev
        )
        assert status2 == 200
        assert data2["autosave_revision"] == first_rev + 1

    def test_sets_last_autosaved_at(self, auth_client, make_user_token, app):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        _autosave(auth_client, token, draft["slug"])
        with app.app_context():
            from backend.extensions import db
            from backend.models.post import Post

            post = db.session.execute(
                db.select(Post).filter_by(slug=draft["slug"])
            ).scalar_one()
            assert post.last_autosaved_at is not None

    def test_does_not_bump_version(self, auth_client, make_user_token, app):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        with app.app_context():
            from backend.extensions import db
            from backend.models.post import Post

            before = (
                db.session.execute(db.select(Post).filter_by(slug=draft["slug"]))
                .scalar_one()
                .version
            )
        _autosave(auth_client, token, draft["slug"], title="Changed Title")
        with app.app_context():
            from backend.extensions import db
            from backend.models.post import Post

            after = (
                db.session.execute(db.select(Post).filter_by(slug=draft["slug"]))
                .scalar_one()
                .version
            )
        assert before == after

    def test_response_contains_slug_and_saved_at_iso(
        self, auth_client, make_user_token
    ):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        _, data = _autosave(auth_client, token, draft["slug"])
        assert "slug" in data
        assert "saved_at_iso" in data
        assert data["slug"] == draft["slug"]

    def test_updates_tags_when_provided(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        draft = _create_draft(auth_client, token)
        _autosave(auth_client, token, draft["slug"], tags=["python", "flask"])
        resp = auth_client.get(f"/api/posts/{draft['slug']}", headers=_auth(token))
        tag_names = [t["name"] for t in resp.get_json().get("tags", [])]
        assert "python" in tag_names
        assert "flask" in tag_names

    def test_editor_can_autosave_others_draft(self, auth_client, make_user_token):
        _, contrib_token = make_user_token(role="contributor")
        _, editor_token = make_user_token(role="editor")
        draft = _create_draft(auth_client, contrib_token)
        status, data = _autosave(
            auth_client, editor_token, draft["slug"], title="Editor fix"
        )
        assert status == 200
        assert data["ok"] is True


# ── Auth / permissions ─────────────────────────────────────────────────────────


class TestAutosaveAuth:
    def test_401_without_auth(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        resp = auth_client.post(
            f"/api/posts/{draft['slug']}/autosave",
            json={"autosave_revision": 0},
        )
        assert resp.status_code == 401

    def test_403_for_different_contributor(self, auth_client, make_user_token):
        _, owner_token = make_user_token(role="contributor")
        _, other_token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, owner_token)
        status, _ = _autosave(auth_client, other_token, draft["slug"], title="Hijack")
        assert status == 403

    def test_404_for_nonexistent_post(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        status, _ = _autosave(auth_client, token, "does-not-exist")
        assert status == 404


# ── Optimistic concurrency (409) ───────────────────────────────────────────────


class TestAutosaveConflict:
    def test_409_on_stale_revision(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        # Advance the revision by one successful autosave
        _autosave(auth_client, token, draft["slug"])
        # Now send a stale revision (still 0)
        status, data = _autosave(auth_client, token, draft["slug"], autosave_revision=0)
        assert status == 409
        assert data.get("conflict") is True

    def test_409_response_includes_current_revision(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token)
        _, first = _autosave(auth_client, token, draft["slug"])
        current_rev = first["autosave_revision"]
        # Send something stale
        status, data = _autosave(
            auth_client, token, draft["slug"], autosave_revision=current_rev - 1
        )
        assert status == 409
        assert data["autosave_revision"] == current_rev


# ── 422 — can only autosave drafts ────────────────────────────────────────────


class TestAutosaveNonDraft:
    def test_422_on_published_post(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        draft = _create_draft(auth_client, token)
        # Publish first
        auth_client.post(f"/api/posts/{draft['slug']}/publish", headers=_auth(token))
        status, _ = _autosave(auth_client, token, draft["slug"])
        assert status == 422


# ── Partial-update semantics ───────────────────────────────────────────────────


class TestAutosavePartialUpdate:
    def test_omitting_title_leaves_it_unchanged(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token, title="Keep Me")
        # Autosave only the body; do not send title
        _autosave(auth_client, token, draft["slug"], markdown_body="New body.")
        resp = auth_client.get(f"/api/posts/{draft['slug']}", headers=_auth(token))
        assert resp.get_json()["title"] == "Keep Me"

    def test_omitting_body_leaves_it_unchanged(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        draft = _create_draft(auth_client, token, markdown_body="Original body.")
        _autosave(auth_client, token, draft["slug"], title="New Title Only")
        resp = auth_client.get(f"/api/posts/{draft['slug']}", headers=_auth(token))
        assert resp.get_json()["markdown_body"] == "Original body."
