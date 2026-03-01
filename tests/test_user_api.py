"""Tests for the user profile & follow API."""

from __future__ import annotations

import pytest

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
def carol(make_user_token, db_session):
    user, tok = make_user_token("carol@example.com", "carol")
    return user, tok


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── GET /api/users/<username> ─────────────────────────────────────────────────


class TestGetProfile:
    def test_public_profile_returns_200(self, auth_client, alice, db_session):
        user, _ = alice
        resp = auth_client.get(f"/api/users/{user.username}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["username"] == user.username
        assert "follower_count" in data
        assert "following_count" in data
        assert "post_count" in data
        assert "is_following" not in data  # no viewer

    def test_authenticated_viewer_gets_is_following_false(
        self, auth_client, alice, bob, db_session
    ):
        alice_user, alice_tok = alice
        bob_user, _ = bob
        resp = auth_client.get(
            f"/api/users/{bob_user.username}", headers=_headers(alice_tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_following"] is False

    def test_authenticated_viewer_gets_is_following_true(
        self, auth_client, alice, bob, db_session
    ):
        from backend.services.user_service import UserService

        alice_user, alice_tok = alice
        bob_user, _ = bob
        UserService.follow(alice_user.id, bob_user.id)
        resp = auth_client.get(
            f"/api/users/{bob_user.username}", headers=_headers(alice_tok)
        )
        assert resp.status_code == 200
        assert resp.get_json()["is_following"] is True

    def test_unknown_username_returns_404(self, auth_client, db_session):
        resp = auth_client.get("/api/users/nobody_here")
        assert resp.status_code == 404

    def test_self_profile_has_no_is_following(self, auth_client, alice, db_session):
        alice_user, alice_tok = alice
        resp = auth_client.get(
            f"/api/users/{alice_user.username}", headers=_headers(alice_tok)
        )
        assert resp.status_code == 200
        # Viewer is same as profile owner → no is_following field
        assert "is_following" not in resp.get_json()


# ── PATCH /api/users/<username> ───────────────────────────────────────────────


class TestUpdateProfile:
    def test_update_own_bio(self, auth_client, alice, db_session):
        user, tok = alice
        resp = auth_client.patch(
            f"/api/users/{user.username}",
            json={"bio": "Updated bio"},
            headers=_headers(tok),
        )
        assert resp.status_code == 200
        assert resp.get_json()["bio"] == "Updated bio"

    def test_update_multiple_fields(self, auth_client, alice, db_session):
        user, tok = alice
        payload = {
            "display_name": "Alice A.",
            "location": "Berlin",
            "tech_stack": "Python,Flask",
            "website_url": "https://alice.dev",
            "github_url": "https://github.com/alice",
        }
        resp = auth_client.patch(
            f"/api/users/{user.username}",
            json=payload,
            headers=_headers(tok),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["display_name"] == "Alice A."
        assert data["location"] == "Berlin"
        assert data["tech_stack"] == "python, flask"

    def test_unknown_fields_ignored(self, auth_client, alice, db_session):
        user, tok = alice
        resp = auth_client.patch(
            f"/api/users/{user.username}",
            json={"role": "admin", "bio": "Legit"},
            headers=_headers(tok),
        )
        assert resp.status_code == 200
        # role should NOT have changed
        assert resp.get_json()["role"] != "admin"
        assert resp.get_json()["bio"] == "Legit"

    def test_cannot_update_other_user(self, auth_client, alice, bob, db_session):
        _, alice_tok = alice
        bob_user, _ = bob
        resp = auth_client.patch(
            f"/api/users/{bob_user.username}",
            json={"bio": "Hijack"},
            headers=_headers(alice_tok),
        )
        assert resp.status_code == 403

    def test_requires_auth(self, auth_client, alice, db_session):
        user, _ = alice
        resp = auth_client.patch(f"/api/users/{user.username}", json={"bio": "x"})
        assert resp.status_code == 401

    def test_update_unknown_user_returns_404(self, auth_client, alice, db_session):
        _, tok = alice
        resp = auth_client.patch(
            "/api/users/nobody_here",
            json={"bio": "x"},
            headers=_headers(tok),
        )
        assert resp.status_code == 404


# ── POST /api/users/<username>/follow ─────────────────────────────────────────


class TestFollowEndpoint:
    def test_follow_returns_200(self, auth_client, alice, bob, db_session):
        alice_user, alice_tok = alice
        bob_user, _ = bob
        resp = auth_client.post(
            f"/api/users/{bob_user.username}/follow", headers=_headers(alice_tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["following"] is True
        assert data["follower_count"] == 1

    def test_follow_self_returns_400(self, auth_client, alice, db_session):
        user, tok = alice
        resp = auth_client.post(
            f"/api/users/{user.username}/follow", headers=_headers(tok)
        )
        assert resp.status_code == 400

    def test_duplicate_follow_returns_409(self, auth_client, alice, bob, db_session):
        alice_user, alice_tok = alice
        bob_user, _ = bob
        auth_client.post(
            f"/api/users/{bob_user.username}/follow", headers=_headers(alice_tok)
        )
        resp = auth_client.post(
            f"/api/users/{bob_user.username}/follow", headers=_headers(alice_tok)
        )
        assert resp.status_code == 409

    def test_follow_unknown_user_returns_404(self, auth_client, alice, db_session):
        _, tok = alice
        resp = auth_client.post("/api/users/nobody_here/follow", headers=_headers(tok))
        assert resp.status_code == 404

    def test_follow_requires_auth(self, auth_client, bob, db_session):
        bob_user, _ = bob
        resp = auth_client.post(f"/api/users/{bob_user.username}/follow")
        assert resp.status_code == 401


# ── DELETE /api/users/<username>/follow ───────────────────────────────────────


class TestUnfollowEndpoint:
    def test_unfollow_returns_200(self, auth_client, alice, bob, db_session):
        from backend.services.user_service import UserService

        alice_user, alice_tok = alice
        bob_user, _ = bob
        UserService.follow(alice_user.id, bob_user.id)
        resp = auth_client.delete(
            f"/api/users/{bob_user.username}/follow", headers=_headers(alice_tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["following"] is False
        assert data["follower_count"] == 0

    def test_unfollow_not_following_returns_404(
        self, auth_client, alice, bob, db_session
    ):
        alice_user, alice_tok = alice
        bob_user, _ = bob
        resp = auth_client.delete(
            f"/api/users/{bob_user.username}/follow", headers=_headers(alice_tok)
        )
        assert resp.status_code == 404

    def test_unfollow_unknown_user_returns_404(self, auth_client, alice, db_session):
        _, tok = alice
        resp = auth_client.delete(
            "/api/users/nobody_here/follow", headers=_headers(tok)
        )
        assert resp.status_code == 404

    def test_unfollow_requires_auth(self, auth_client, bob, db_session):
        bob_user, _ = bob
        resp = auth_client.delete(f"/api/users/{bob_user.username}/follow")
        assert resp.status_code == 401


# ── GET /api/users/<username>/followers ───────────────────────────────────────


class TestFollowersEndpoint:
    def test_empty_list(self, auth_client, alice, db_session):
        user, _ = alice
        resp = auth_client.get(f"/api/users/{user.username}/followers")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["users"] == []

    def test_returns_followers(self, auth_client, alice, bob, carol, db_session):
        from backend.services.user_service import UserService

        alice_user, _ = alice
        bob_user, _ = bob
        carol_user, _ = carol
        UserService.follow(bob_user.id, alice_user.id)
        UserService.follow(carol_user.id, alice_user.id)
        resp = auth_client.get(f"/api/users/{alice_user.username}/followers")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 2
        usernames = {u["username"] for u in data["users"]}
        assert "bob" in usernames
        assert "carol" in usernames

    def test_pagination(self, auth_client, alice, bob, carol, db_session):
        from backend.services.user_service import UserService

        alice_user, _ = alice
        bob_user, _ = bob
        carol_user, _ = carol
        UserService.follow(bob_user.id, alice_user.id)
        UserService.follow(carol_user.id, alice_user.id)
        resp = auth_client.get(
            f"/api/users/{alice_user.username}/followers?page=1&per_page=1"
        )
        data = resp.get_json()
        assert data["total"] == 2
        assert len(data["users"]) == 1
        assert data["pages"] == 2

    def test_unknown_user_returns_404(self, auth_client, db_session):
        resp = auth_client.get("/api/users/nobody_here/followers")
        assert resp.status_code == 404


# ── GET /api/users/<username>/following ───────────────────────────────────────


class TestFollowingEndpoint:
    def test_empty_list(self, auth_client, alice, db_session):
        user, _ = alice
        resp = auth_client.get(f"/api/users/{user.username}/following")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 0

    def test_returns_following(self, auth_client, alice, bob, carol, db_session):
        from backend.services.user_service import UserService

        alice_user, _ = alice
        bob_user, _ = bob
        carol_user, _ = carol
        UserService.follow(alice_user.id, bob_user.id)
        UserService.follow(alice_user.id, carol_user.id)
        resp = auth_client.get(f"/api/users/{alice_user.username}/following")
        data = resp.get_json()
        assert data["total"] == 2
        usernames = {u["username"] for u in data["users"]}
        assert "bob" in usernames
        assert "carol" in usernames

    def test_unknown_user_returns_404(self, auth_client, db_session):
        resp = auth_client.get("/api/users/nobody_here/following")
        assert resp.status_code == 404


# ── GET /api/users/<username>/posts ───────────────────────────────────────────


class TestUserPostsEndpoint:
    def test_empty_list(self, auth_client, alice, db_session):
        user, _ = alice
        resp = auth_client.get(f"/api/users/{user.username}/posts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["posts"] == []

    def test_returns_published_posts(self, auth_client, alice, db_session):
        from backend.extensions import db
        from backend.models.post import Post, PostStatus

        user, _ = alice
        pub = Post(
            author_id=user.id,
            title="A Post",
            slug="a-post-alice-api",
            markdown_body="# A Post",
            status=PostStatus.published,
        )
        db.session.add(pub)
        db.session.commit()
        resp = auth_client.get(f"/api/users/{user.username}/posts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert data["posts"][0]["slug"] == "a-post-alice-api"

    def test_draft_not_included(self, auth_client, alice, db_session):
        from backend.extensions import db
        from backend.models.post import Post, PostStatus

        user, _ = alice
        draft = Post(
            author_id=user.id,
            title="Draft Post",
            slug="draft-post-alice-api",
            markdown_body="# Draft",
            status=PostStatus.draft,
        )
        db.session.add(draft)
        db.session.commit()
        resp = auth_client.get(f"/api/users/{user.username}/posts")
        assert resp.get_json()["total"] == 0

    def test_unknown_user_returns_404(self, auth_client, db_session):
        resp = auth_client.get("/api/users/nobody_here/posts")
        assert resp.status_code == 404
