"""Tests for the bookmark API endpoints."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, tok = make_user_token("alice@example.com", "alice")
    return user, tok


@pytest.fixture()
def bob(make_user_token, db_session):
    user, tok = make_user_token("bob@example.com", "bob")
    return user, tok


@pytest.fixture()
def pub_post(alice, db_session):
    from backend.extensions import db

    user, _ = alice
    post = Post(
        author_id=user.id,
        title="Bookmarkable Post",
        slug="bm-api-post",
        markdown_body="# Content",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── POST /api/posts/<slug>/bookmark ───────────────────────────────────────────


class TestAddBookmark:
    def test_add_returns_200(self, auth_client, bob, pub_post, db_session):
        _, tok = bob
        resp = auth_client.post(f"/api/posts/{pub_post.slug}/bookmark", headers=_h(tok))
        assert resp.status_code == 200
        assert resp.get_json()["bookmarked"] is True

    def test_duplicate_returns_409(self, auth_client, bob, pub_post, db_session):
        _, tok = bob
        auth_client.post(f"/api/posts/{pub_post.slug}/bookmark", headers=_h(tok))
        resp = auth_client.post(f"/api/posts/{pub_post.slug}/bookmark", headers=_h(tok))
        assert resp.status_code == 409

    def test_unknown_post_returns_404(self, auth_client, bob, db_session):
        _, tok = bob
        resp = auth_client.post("/api/posts/no-such-post/bookmark", headers=_h(tok))
        assert resp.status_code == 404

    def test_requires_auth(self, auth_client, pub_post, db_session):
        resp = auth_client.post(f"/api/posts/{pub_post.slug}/bookmark")
        assert resp.status_code == 401


# ── DELETE /api/posts/<slug>/bookmark ─────────────────────────────────────────


class TestRemoveBookmark:
    def test_remove_returns_200(self, auth_client, bob, pub_post, db_session):
        from backend.services.bookmark_service import BookmarkService

        bob_user, tok = bob
        BookmarkService.add(bob_user.id, pub_post.id)
        resp = auth_client.delete(
            f"/api/posts/{pub_post.slug}/bookmark", headers=_h(tok)
        )
        assert resp.status_code == 200
        assert resp.get_json()["bookmarked"] is False

    def test_not_bookmarked_returns_404(self, auth_client, bob, pub_post, db_session):
        _, tok = bob
        resp = auth_client.delete(
            f"/api/posts/{pub_post.slug}/bookmark", headers=_h(tok)
        )
        assert resp.status_code == 404

    def test_requires_auth(self, auth_client, pub_post, db_session):
        resp = auth_client.delete(f"/api/posts/{pub_post.slug}/bookmark")
        assert resp.status_code == 401


# ── GET /api/bookmarks/ ───────────────────────────────────────────────────────


class TestListBookmarks:
    def test_empty_list(self, auth_client, bob, db_session):
        _, tok = bob
        resp = auth_client.get("/api/bookmarks/", headers=_h(tok))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["posts"] == []

    def test_returns_bookmarked_posts(self, auth_client, bob, pub_post, db_session):
        bob_user, tok = bob
        from backend.services.bookmark_service import BookmarkService

        BookmarkService.add(bob_user.id, pub_post.id)
        resp = auth_client.get("/api/bookmarks/", headers=_h(tok))
        data = resp.get_json()
        assert data["total"] == 1
        assert data["posts"][0]["slug"] == pub_post.slug

    def test_only_own_bookmarks(self, auth_client, alice, bob, pub_post, db_session):
        alice_user, _ = alice
        _, bob_tok = bob
        from backend.services.bookmark_service import BookmarkService

        BookmarkService.add(alice_user.id, pub_post.id)
        resp = auth_client.get("/api/bookmarks/", headers=_h(bob_tok))
        assert resp.get_json()["total"] == 0

    def test_pagination(self, auth_client, alice, bob, db_session):
        from backend.extensions import db as _db
        from backend.services.bookmark_service import BookmarkService

        alice_user, _ = alice
        bob_user, bob_tok = bob
        for i in range(3):
            p = Post(
                author_id=alice_user.id,
                title=f"BM Paged {i}",
                slug=f"bm-paged-{i}",
                markdown_body="# Hi",
                status=PostStatus.published,
            )
            _db.session.add(p)
            _db.session.flush()
            BookmarkService.add(bob_user.id, p.id)
        _db.session.commit()
        resp = auth_client.get("/api/bookmarks/?page=1&per_page=2", headers=_h(bob_tok))
        data = resp.get_json()
        assert data["total"] == 3
        assert len(data["posts"]) == 2
        assert data["pages"] == 2

    def test_requires_auth(self, auth_client, db_session):
        resp = auth_client.get("/api/bookmarks/")
        assert resp.status_code == 401


# ── post_dict includes has_bookmarked ─────────────────────────────────────────


class TestPostDictBookmarkField:
    def test_has_bookmarked_false_when_not_bookmarked(
        self, auth_client, bob, pub_post, db_session
    ):
        _, tok = bob
        resp = auth_client.get(f"/api/posts/{pub_post.slug}", headers=_h(tok))
        assert resp.get_json()["has_bookmarked"] is False

    def test_has_bookmarked_true_after_bookmark(
        self, auth_client, bob, pub_post, db_session
    ):
        bob_user, tok = bob
        from backend.services.bookmark_service import BookmarkService

        BookmarkService.add(bob_user.id, pub_post.id)
        resp = auth_client.get(f"/api/posts/{pub_post.slug}", headers=_h(tok))
        assert resp.get_json()["has_bookmarked"] is True

    def test_no_has_bookmarked_when_anonymous(self, auth_client, pub_post, db_session):
        resp = auth_client.get(f"/api/posts/{pub_post.slug}")
        assert "has_bookmarked" not in resp.get_json()
