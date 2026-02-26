"""Search API endpoint tests.

Uses the ``auth_client`` and ``make_user_token`` fixtures.
"""

from __future__ import annotations

# ── Helpers ────────────────────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_published_post(client, token: str, title: str, body: str = "Body text") -> dict:
    """Create and publish a post, return the post dict."""
    post = client.post(
        "/api/posts/",
        json={"title": title, "markdown_body": body},
        headers=_auth(token),
    ).get_json()
    client.post(f"/api/posts/{post['slug']}/publish", json={}, headers=_auth(token))
    return post


# ── GET /api/search/ ──────────────────────────────────────────────────────────


class TestSearchApi:
    def test_empty_query_returns_200_empty(self, auth_client):
        resp = auth_client.get("/api/search/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["posts"] == []
        assert data["query"] == ""

    def test_whitespace_query_returns_empty(self, auth_client):
        resp = auth_client.get("/api/search/?q=+")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 0

    def test_title_match_returned(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_published_post(auth_client, token, "Flask Deep Dive")
        resp = auth_client.get("/api/search/?q=Flask")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert data["posts"][0]["title"] == "Flask Deep Dive"

    def test_body_match_returned(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_published_post(
            auth_client, token, "Unrelated Title", body="SQLAlchemy is great"
        )
        resp = auth_client.get("/api/search/?q=SQLAlchemy")
        data = resp.get_json()
        assert data["total"] == 1

    def test_draft_not_returned(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        auth_client.post(
            "/api/posts/",
            json={"title": "Hidden Draft", "markdown_body": "secret"},
            headers=_auth(token),
        )
        resp = auth_client.get("/api/search/?q=Hidden+Draft")
        assert resp.get_json()["total"] == 0

    def test_response_includes_excerpt(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_published_post(
            auth_client, token, "Excerpt Test", body="This post talks about Redis caching"
        )
        resp = auth_client.get("/api/search/?q=Redis")
        data = resp.get_json()
        assert data["total"] == 1
        assert "excerpt" in data["posts"][0]
        assert "Redis" in data["posts"][0]["excerpt"]

    def test_response_schema(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_published_post(auth_client, token, "Schema Check Post")
        resp = auth_client.get("/api/search/?q=Schema+Check")
        data = resp.get_json()
        assert "query" in data
        assert "posts" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "pages" in data

    def test_pagination_params_respected(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        for i in range(5):
            _make_published_post(auth_client, token, f"Python Article {i}")
        resp = auth_client.get("/api/search/?q=Python+Article&per_page=2")
        data = resp.get_json()
        assert data["total"] == 5
        assert len(data["posts"]) == 2
        assert data["pages"] == 3

    def test_page_2_returns_next_slice(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        for i in range(4):
            _make_published_post(auth_client, token, f"Go Tutorial {i}")
        resp_p1 = auth_client.get("/api/search/?q=Go+Tutorial&per_page=2&page=1")
        resp_p2 = auth_client.get("/api/search/?q=Go+Tutorial&per_page=2&page=2")
        ids_p1 = {p["id"] for p in resp_p1.get_json()["posts"]}
        ids_p2 = {p["id"] for p in resp_p2.get_json()["posts"]}
        assert ids_p1.isdisjoint(ids_p2)

    def test_no_match_returns_empty_posts(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_published_post(auth_client, token, "Irrelevant Post")
        resp = auth_client.get("/api/search/?q=xyzzynotfound")
        data = resp.get_json()
        assert data["total"] == 0
        assert data["posts"] == []
        assert data["pages"] == 0


# ── GET /search/ (SSR) ────────────────────────────────────────────────────────


class TestSearchSsr:
    def test_search_page_loads(self, auth_client):
        resp = auth_client.get("/search/")
        assert resp.status_code == 200

    def test_search_page_with_query(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_published_post(auth_client, token, "SSR Search Post", body="SSR content")
        resp = auth_client.get("/search/?q=SSR+Search")
        assert resp.status_code == 200
        assert b"SSR Search Post" in resp.data

    def test_empty_search_shows_form(self, auth_client):
        resp = auth_client.get("/search/")
        assert resp.status_code == 200
        assert b"search" in resp.data.lower()
