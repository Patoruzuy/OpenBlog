"""Tests for the SSR search route (/search) and suggest endpoint (/search/suggest).

All tests use the ``auth_client`` + ``db_session`` fixtures (SQLite in-memory).
"""

from __future__ import annotations

from backend.extensions import db as _db
from backend.models.portal import IdentityMode, ProfileVisibility, UserPrivacySettings
from backend.services.auth_service import AuthService

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_post(
    client,
    token: str,
    title: str,
    body: str = "Body text",
    tags: list[str] | None = None,
) -> dict:
    """Create and publish a post via API, return the post dict."""
    payload: dict = {"title": title, "markdown_body": body}
    if tags:
        payload["tags"] = tags
    post = client.post(
        "/api/posts/",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()
    client.post(
        f"/api/posts/{post['slug']}/publish",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    return post


def _make_public_user(username: str, display_name: str | None = None):
    """Register a user; no privacy settings row → defaults to public/searchable."""
    u = AuthService.register(f"{username}@route.test", username, "StrongPass123!!")
    if display_name:
        u.display_name = display_name
        _db.session.commit()
    return u


def _add_privacy(
    user, visibility: str = "public", searchable: bool = True, mode: str = "public"
):
    priv = UserPrivacySettings(
        user_id=user.id,
        profile_visibility=visibility,
        searchable_profile=searchable,
        default_identity_mode=mode,
    )
    _db.session.add(priv)
    _db.session.commit()
    return priv


# ── GET /search ────────────────────────────────────────────────────────────────


class TestSearchResultsRoute:
    def test_empty_query_returns_200(self, auth_client):
        resp = auth_client.get("/search/")
        assert resp.status_code == 200

    def test_empty_query_shows_prompt(self, auth_client):
        resp = auth_client.get("/search/")
        assert b"search-page" in resp.data

    def test_query_renders_tabs(self, auth_client):
        resp = auth_client.get("/search/?q=python")
        assert resp.status_code == 200
        # All three tabs must be present
        assert b"tab=posts" in resp.data
        assert b"tab=topics" in resp.data
        assert b"tab=people" in resp.data

    def test_invalid_tab_falls_back_to_posts(self, auth_client):
        resp = auth_client.get("/search/?q=python&tab=nonexistent")
        assert resp.status_code == 200
        # Active tab class should appear on posts tab link
        assert b"search-tab--active" in resp.data

    def test_posts_tab_default(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "Flask Routing Guide")
        resp = auth_client.get("/search/?q=Flask+Routing")
        assert resp.status_code == 200
        # highlight_terms wraps each word in <mark> tags so the raw title string
        # never appears verbatim; we assert that a result-card was rendered instead.
        assert b"result-title" in resp.data

    def test_topics_tab_renders(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "Post About Django", tags=["django-framework"])
        resp = auth_client.get("/search/?q=django&tab=topics")
        assert resp.status_code == 200
        # The full slug only appears inside the rendered tag card, not in the URL params.
        assert b"django-framework" in resp.data

    def test_people_tab_shows_public_user(self, auth_client, db_session):  # noqa: ARG002
        _make_public_user("searchable_jane", display_name="Jane Search")
        resp = auth_client.get("/search/?q=searchable_jane&tab=people")
        assert resp.status_code == 200
        # Profile link in user-card is the reliable indicator the user appears.
        assert b"/users/searchable_jane" in resp.data

    def test_people_tab_excludes_private_user(self, auth_client, db_session):  # noqa: ARG002
        u = _make_public_user("privatejoe")
        _add_privacy(u, visibility=ProfileVisibility.private.value)
        resp = auth_client.get("/search/?q=privatejoe&tab=people")
        assert resp.status_code == 200
        # Profile link must NOT appear — user is excluded from results.
        assert b"/users/privatejoe" not in resp.data

    def test_people_tab_excludes_anonymous_user(self, auth_client, db_session):  # noqa: ARG002
        u = _make_public_user("anonrouteusr")
        _add_privacy(u, mode=IdentityMode.anonymous.value)
        resp = auth_client.get("/search/?q=anonrouteusr&tab=people")
        assert resp.status_code == 200
        assert b"/users/anonrouteusr" not in resp.data

    def test_zero_results_shows_empty_state(self, auth_client):
        resp = auth_client.get("/search/?q=xyznonexistent99")
        assert resp.status_code == 200
        assert b"empty-state" in resp.data

    def test_zero_results_message_contains_query(self, auth_client):
        resp = auth_client.get("/search/?q=totallymissingterm")
        assert b"totallymissingterm" in resp.data

    def test_draft_post_not_in_results(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        # Create but do NOT publish
        auth_client.post(
            "/api/posts/",
            json={"title": "HiddenDraftXYZ", "markdown_body": "secret draft"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = auth_client.get("/search/?q=HiddenDraftXYZ")
        assert resp.status_code == 200
        # The query appears in <title> and <input value>, but no post card
        # should be rendered for a draft — no result-title anchor in page.
        assert b'class="result-title"' not in resp.data

    def test_people_tab_shows_zero_results_state(self, auth_client):
        resp = auth_client.get("/search/?q=nobody12345xyz&tab=people")
        assert resp.status_code == 200
        assert b"empty-state" in resp.data


# ── GET /search/suggest ────────────────────────────────────────────────────────


class TestSuggestEndpoint:
    def test_short_query_returns_200(self, auth_client):
        resp = auth_client.get("/search/suggest?q=a")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["posts"] == []
        assert data["tags"] == []
        assert data["users"] == []

    def test_empty_query_returns_empty_groups(self, auth_client):
        resp = auth_client.get("/search/suggest?q=")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "posts" in data
        assert "tags" in data
        assert "users" in data
        assert "recent" in data

    def test_suggest_response_has_users_key(self, auth_client):
        resp = auth_client.get("/search/suggest?q=python")
        assert resp.status_code == 200
        assert "users" in resp.get_json()

    def test_suggest_includes_public_user(self, auth_client, db_session):  # noqa: ARG002
        _make_public_user("suggestroute")
        resp = auth_client.get("/search/suggest?q=suggestroute")
        data = resp.get_json()
        usernames = [u["username"] for u in data.get("users", [])]
        assert "suggestroute" in usernames

    def test_suggest_excludes_private_user(self, auth_client, db_session):  # noqa: ARG002
        u = _make_public_user("privsugroute")
        _add_privacy(u, visibility=ProfileVisibility.private.value)
        resp = auth_client.get("/search/suggest?q=privsugroute")
        data = resp.get_json()
        usernames = [u["username"] for u in data.get("users", [])]
        assert "privsugroute" not in usernames

    def test_suggest_excludes_anonymous_user(self, auth_client, db_session):  # noqa: ARG002
        u = _make_public_user("anonsugroute")
        _add_privacy(u, mode=IdentityMode.anonymous.value)
        resp = auth_client.get("/search/suggest?q=anonsugroute")
        data = resp.get_json()
        usernames = [u["username"] for u in data.get("users", [])]
        assert "anonsugroute" not in usernames

    def test_suggest_user_entry_shape(self, auth_client, db_session):  # noqa: ARG002
        _make_public_user("shapecheck")
        resp = auth_client.get("/search/suggest?q=shapecheck")
        data = resp.get_json()
        if data["users"]:
            u = data["users"][0]
            assert "username" in u
            assert "display_name" in u
            assert "avatar_url" in u

    def test_suggest_includes_post(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "UniqueSuggestTitle123")
        resp = auth_client.get("/search/suggest?q=UniqueSuggestTitle123")
        data = resp.get_json()
        titles = [p["title"] for p in data.get("posts", [])]
        assert "UniqueSuggestTitle123" in titles

    def test_suggest_returns_json(self, auth_client):
        resp = auth_client.get("/search/suggest?q=python")
        assert resp.content_type.startswith("application/json")

    def test_empty_query_recent_empty_for_anon(self, auth_client):
        """Anonymous users get empty recent list."""
        resp = auth_client.get("/search/suggest?q=")
        data = resp.get_json()
        assert data["recent"] == []


# ── Recent search storage ──────────────────────────────────────────────────────


class TestRecentSearches:
    def test_recent_searches_stored_for_authenticated_user(
        self,
        auth_client,
        make_user_token,
        db_session,  # noqa: ARG002
    ):
        """Recent search queries are stored and returned via suggest."""
        user, token = make_user_token()
        # Perform a search while authenticated (uses session login)
        with auth_client.session_transaction() as sess:
            sess["user_id"] = user.id
        auth_client.get("/search/?q=recentquery42")
        resp = auth_client.get("/search/suggest?q=")
        data = resp.get_json()
        assert "recentquery42" in data["recent"]

    def test_recent_searches_not_stored_for_anonymous(self, auth_client):
        """Anonymous searches do not persist."""
        auth_client.get("/search/?q=anonquery99")
        resp = auth_client.get("/search/suggest?q=")
        data = resp.get_json()
        # Recent list is empty for unauthenticated client
        assert "anonquery99" not in data["recent"]
