"""Tests for the vote API endpoints."""

from __future__ import annotations

import pytest

from backend.models.comment import Comment
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
        title="Vote Target Post",
        slug="vote-target-post",
        markdown_body="# Vote me",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


@pytest.fixture()
def alice_comment(alice, pub_post, db_session):
    from backend.extensions import db

    user, _ = alice
    c = Comment(post_id=pub_post.id, author_id=user.id, body="Alice's comment")
    db.session.add(c)
    db.session.commit()
    return c


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── POST /api/posts/<slug>/vote ───────────────────────────────────────────────


class TestVotePost:
    def test_upvote_returns_200(self, auth_client, alice, bob, pub_post, db_session):
        _, bob_tok = bob
        resp = auth_client.post(f"/api/posts/{pub_post.slug}/vote", headers=_h(bob_tok))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["voted"] is True
        assert data["vote_count"] == 1

    def test_self_vote_returns_400(self, auth_client, alice, pub_post, db_session):
        _, alice_tok = alice
        resp = auth_client.post(
            f"/api/posts/{pub_post.slug}/vote", headers=_h(alice_tok)
        )
        assert resp.status_code == 400

    def test_duplicate_returns_409(self, auth_client, bob, pub_post, db_session):
        _, bob_tok = bob
        auth_client.post(f"/api/posts/{pub_post.slug}/vote", headers=_h(bob_tok))
        resp = auth_client.post(f"/api/posts/{pub_post.slug}/vote", headers=_h(bob_tok))
        assert resp.status_code == 409

    def test_requires_auth(self, auth_client, pub_post, db_session):
        resp = auth_client.post(f"/api/posts/{pub_post.slug}/vote")
        assert resp.status_code == 401

    def test_unknown_post_returns_404(self, auth_client, bob, db_session):
        _, tok = bob
        resp = auth_client.post("/api/posts/no-such-post/vote", headers=_h(tok))
        assert resp.status_code == 404


# ── DELETE /api/posts/<slug>/vote ─────────────────────────────────────────────


class TestUnvotePost:
    def test_unvote_returns_200(self, auth_client, alice, bob, pub_post, db_session):
        from backend.services.vote_service import VoteService

        bob_user, bob_tok = bob
        VoteService.upvote(bob_user.id, "post", pub_post.id)
        resp = auth_client.delete(
            f"/api/posts/{pub_post.slug}/vote", headers=_h(bob_tok)
        )
        assert resp.status_code == 200
        assert resp.get_json()["voted"] is False

    def test_unvote_not_voted_returns_404(self, auth_client, bob, pub_post, db_session):
        _, bob_tok = bob
        resp = auth_client.delete(
            f"/api/posts/{pub_post.slug}/vote", headers=_h(bob_tok)
        )
        assert resp.status_code == 404

    def test_requires_auth(self, auth_client, pub_post, db_session):
        resp = auth_client.delete(f"/api/posts/{pub_post.slug}/vote")
        assert resp.status_code == 401


# ── POST /api/comments/<id>/vote ──────────────────────────────────────────────


class TestVoteComment:
    def test_upvote_comment_returns_200(
        self, auth_client, bob, alice_comment, db_session
    ):
        _, bob_tok = bob
        resp = auth_client.post(
            f"/api/comments/{alice_comment.id}/vote", headers=_h(bob_tok)
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["voted"] is True
        assert data["vote_count"] == 1

    def test_self_vote_comment_returns_400(
        self, auth_client, alice, alice_comment, db_session
    ):
        _, alice_tok = alice
        resp = auth_client.post(
            f"/api/comments/{alice_comment.id}/vote", headers=_h(alice_tok)
        )
        assert resp.status_code == 400

    def test_unknown_comment_returns_404(self, auth_client, bob, db_session):
        _, tok = bob
        resp = auth_client.post("/api/comments/99999/vote", headers=_h(tok))
        assert resp.status_code == 404

    def test_requires_auth(self, auth_client, alice_comment, db_session):
        resp = auth_client.post(f"/api/comments/{alice_comment.id}/vote")
        assert resp.status_code == 401


# ── DELETE /api/comments/<id>/vote ────────────────────────────────────────────


class TestUnvoteComment:
    def test_unvote_comment_returns_200(
        self, auth_client, bob, alice_comment, db_session
    ):
        from backend.services.vote_service import VoteService

        bob_user, bob_tok = bob
        VoteService.upvote(bob_user.id, "comment", alice_comment.id)
        resp = auth_client.delete(
            f"/api/comments/{alice_comment.id}/vote", headers=_h(bob_tok)
        )
        assert resp.status_code == 200
        assert resp.get_json()["voted"] is False

    def test_unvote_not_voted_returns_404(
        self, auth_client, bob, alice_comment, db_session
    ):
        _, tok = bob
        resp = auth_client.delete(
            f"/api/comments/{alice_comment.id}/vote", headers=_h(tok)
        )
        assert resp.status_code == 404

    def test_requires_auth(self, auth_client, alice_comment, db_session):
        resp = auth_client.delete(f"/api/comments/{alice_comment.id}/vote")
        assert resp.status_code == 401


# ── post_dict includes vote fields ────────────────────────────────────────────


class TestPostDictVoteFields:
    def test_vote_count_in_post_response(self, auth_client, pub_post, db_session):
        resp = auth_client.get(f"/api/posts/{pub_post.slug}")
        data = resp.get_json()
        assert "vote_count" in data
        assert data["vote_count"] == 0

    def test_has_voted_present_when_authenticated(
        self, auth_client, bob, pub_post, db_session
    ):
        _, tok = bob
        resp = auth_client.get(f"/api/posts/{pub_post.slug}", headers=_h(tok))
        data = resp.get_json()
        assert "has_voted" in data
        assert data["has_voted"] is False

    def test_has_voted_true_after_voting(self, auth_client, bob, pub_post, db_session):
        bob_user, tok = bob
        from backend.services.vote_service import VoteService

        VoteService.upvote(bob_user.id, "post", pub_post.id)
        resp = auth_client.get(f"/api/posts/{pub_post.slug}", headers=_h(tok))
        assert resp.get_json()["has_voted"] is True
