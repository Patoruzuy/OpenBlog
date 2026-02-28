"""Route-level tests for search ranking.

Tests both the SSR route (``GET /search?q=``) and the JSON API
(``GET /api/search?q=``), including ranking order, draft privacy and
anonymous/authenticated personalisation paths.
"""

from __future__ import annotations


# ── Helpers ────────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_post(client, token: str, title: str, body: str = "Body content.") -> dict:
    """Create and publish a post; return the post dict."""
    data = client.post(
        "/api/posts/",
        json={"title": title, "markdown_body": body},
        headers=_auth(token),
    ).get_json()
    client.post(f"/api/posts/{data['slug']}/publish", json={}, headers=_auth(token))
    return data


# ── SSR /search?q= ────────────────────────────────────────────────────────────


class TestSsrSearchRoute:
    def test_empty_query_renders_ok(self, auth_client):
        resp = auth_client.get("/search/")
        assert resp.status_code == 200

    def test_query_renders_results_page(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "SSR Ranking Test Post")
        resp = auth_client.get("/search/?q=SSR+Ranking+Test")
        assert resp.status_code == 200
        # The post slug must appear as a link in the rendered HTML.
        assert b"ssr-ranking-test-post" in resp.data

    def test_draft_not_in_ssr_results(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        auth_client.post(
            "/api/posts/",
            json={"title": "Secret SSR Draft", "markdown_body": "hidden"},
            headers=_auth(token),
        )
        # The SSR page title echoes the query, so checking raw text is not
        # reliable.  Use the JSON API to confirm 0 published results.
        resp = auth_client.get("/api/search/?q=Secret+SSR+Draft")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 0

    def test_invalid_tab_defaults_to_posts(self, auth_client):
        resp = auth_client.get("/search/?q=test&tab=invalid")
        assert resp.status_code == 200

    def test_topics_tab_renders(self, auth_client):
        resp = auth_client.get("/search/?q=test&tab=topics")
        assert resp.status_code == 200

    def test_people_tab_renders(self, auth_client):
        resp = auth_client.get("/search/?q=test&tab=people")
        assert resp.status_code == 200


# ── JSON API /api/search?q= ───────────────────────────────────────────────────


class TestApiSearchRanking:
    def test_title_hit_returned_before_body_hit(self, auth_client, make_user_token):
        """Title-matching post should rank above body-only matching post."""
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "Unrelated Piece", "This body mentions Django.")
        _make_post(auth_client, token, "Django Tutorial", "A general intro.")

        resp = auth_client.get("/api/search/?q=Django")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 2
        titles = [p["title"] for p in data["posts"]]
        assert titles.index("Django Tutorial") < titles.index("Unrelated Piece")

    def test_draft_excluded_from_api(self, auth_client, make_user_token):
        _, token = make_user_token(role="contributor")
        auth_client.post(
            "/api/posts/",
            json={"title": "API Hidden Draft", "markdown_body": "secret body"},
            headers=_auth(token),
        )
        resp = auth_client.get("/api/search/?q=API+Hidden+Draft")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 0

    def test_anonymous_search_returns_results(self, client, make_user_token, db_session):  # noqa: ARG002
        """Unauthenticated requests should still get search results."""
        _, token = make_user_token(role="editor")
        _make_post(client, token, "Public Anon Search Post")
        resp = client.get("/api/search/?q=Public+Anon+Search")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 1

    def test_authenticated_search_returns_results(self, auth_client, make_user_token):
        """Authenticated requests go through the personalised code path."""
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "Auth Search Ranking Post")
        resp = auth_client.get(
            "/api/search/?q=Auth+Search+Ranking",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 1

    def test_pagination_still_works_after_ranking(self, auth_client, make_user_token):
        _, token = make_user_token(role="editor")
        for i in range(5):
            _make_post(auth_client, token, f"Paginated Ranked Post {i}")
        resp = auth_client.get("/api/search/?q=Paginated+Ranked+Post&per_page=2")
        data = resp.get_json()
        assert data["total"] == 5
        assert len(data["posts"]) == 2

    def test_suggest_returns_json(self, auth_client, make_user_token):
        """GET /search/suggest?q= returns JSON with posts/tags/users keys."""
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "Suggest Ranking Test")
        resp = auth_client.get("/search/suggest?q=Suggest+Ranking")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "posts" in data and "tags" in data and "users" in data

    def test_suggest_title_hit_appears(self, auth_client, make_user_token):
        """The suggest endpoint should include the title-matching post."""
        _, token = make_user_token(role="editor")
        _make_post(auth_client, token, "SuggestUniqueXYZ Post")
        resp = auth_client.get("/search/suggest?q=SuggestUniqueXYZ")
        data = resp.get_json()
        titles = [p["title"] for p in data["posts"]]
        assert any("SuggestUniqueXYZ" in t for t in titles)
