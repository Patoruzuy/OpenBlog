"""Unit tests for the SSR bookmarks page (GET /bookmarks/)."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):  # noqa: ARG001
    user, _tok = make_user_token("alice@bm-ssr.com", "alice_bm")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):  # noqa: ARG001
    user, _tok = make_user_token("bob@bm-ssr.com", "bob_bm")
    return user


@pytest.fixture()
def pub_post(alice, db_session):  # noqa: ARG001
    """A published post authored by alice."""
    from backend.extensions import db

    post = Post(
        title="Great Article",
        slug="bm-ssr-great-article",
        markdown_body="# Hello",
        author_id=alice.id,
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


def _login(client, user_id: int) -> None:
    """Inject a Flask session cookie to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestBookmarksPage:
    def test_unauthenticated_redirects_to_login(self, auth_client, db_session):
        resp = auth_client.get("/bookmarks/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_authenticated_returns_200(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/bookmarks/")
        assert resp.status_code == 200

    def test_content_type_is_html(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/bookmarks/")
        assert "text/html" in resp.content_type

    def test_empty_state_when_no_bookmarks(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/bookmarks/")
        assert b"haven't bookmarked" in resp.data

    def test_shows_bookmarked_post_title(self, auth_client, bob, pub_post):
        from backend.services.bookmark_service import BookmarkService

        BookmarkService.add(bob.id, pub_post.id)
        _login(auth_client, bob.id)
        resp = auth_client.get("/bookmarks/")
        assert b"Great Article" in resp.data

    def test_does_not_show_other_users_bookmarks(
        self, auth_client, alice, bob, pub_post
    ):
        """Alice's bookmarks must not appear on Bob's page."""
        from backend.services.bookmark_service import BookmarkService

        BookmarkService.add(alice.id, pub_post.id)
        _login(auth_client, bob.id)
        resp = auth_client.get("/bookmarks/")
        assert b"Great Article" not in resp.data

    def test_shows_total_count(self, auth_client, bob, pub_post):
        from backend.services.bookmark_service import BookmarkService

        BookmarkService.add(bob.id, pub_post.id)
        _login(auth_client, bob.id)
        resp = auth_client.get("/bookmarks/")
        assert b"1 saved post" in resp.data

    def test_page_param_defaults_to_one(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/bookmarks/?page=1")
        assert resp.status_code == 200

    def test_invalid_page_clamped_to_one(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/bookmarks/?page=-5")
        assert resp.status_code == 200
