"""Tests for VoteService."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.services.vote_service import VoteError, VoteService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@example.com", "alice")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _ = make_user_token("bob@example.com", "bob")
    return user


@pytest.fixture()
def published_post(alice, db_session):
    from backend.extensions import db

    post = Post(
        author_id=alice.id,
        title="Alice's Post",
        slug="alice-vote-post",
        markdown_body="# Hello",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


@pytest.fixture()
def bob_comment(bob, published_post, db_session):
    from backend.extensions import db
    from backend.models.comment import Comment

    c = Comment(post_id=published_post.id, author_id=bob.id, body="Nice post!")
    db.session.add(c)
    db.session.commit()
    return c


# ── upvote post ───────────────────────────────────────────────────────────────


class TestUpvotePost:
    def test_upvote_increments_count(self, bob, published_post, db_session):
        VoteService.upvote(bob.id, "post", published_post.id)
        assert VoteService.vote_count("post", published_post.id) == 1

    def test_upvote_sets_has_voted(self, bob, published_post, db_session):
        VoteService.upvote(bob.id, "post", published_post.id)
        assert VoteService.has_voted(bob.id, "post", published_post.id) is True

    def test_upvote_credits_author_reputation(self, alice, bob, published_post, db_session):
        from backend.extensions import db as _db

        before = alice.reputation_score or 0
        VoteService.upvote(bob.id, "post", published_post.id)
        _db.session.expire(alice)
        assert alice.reputation_score == before + 1

    def test_self_vote_raises_400(self, alice, published_post, db_session):
        with pytest.raises(VoteError) as exc_info:
            VoteService.upvote(alice.id, "post", published_post.id)
        assert exc_info.value.status_code == 400

    def test_duplicate_vote_raises_409(self, bob, published_post, db_session):
        VoteService.upvote(bob.id, "post", published_post.id)
        with pytest.raises(VoteError) as exc_info:
            VoteService.upvote(bob.id, "post", published_post.id)
        assert exc_info.value.status_code == 409

    def test_vote_nonexistent_post_raises_404(self, bob, db_session):
        with pytest.raises(VoteError) as exc_info:
            VoteService.upvote(bob.id, "post", 99999)
        assert exc_info.value.status_code == 404

    def test_invalid_target_type_raises_400(self, bob, db_session):
        with pytest.raises(VoteError) as exc_info:
            VoteService.upvote(bob.id, "article", 1)
        assert exc_info.value.status_code == 400


# ── unvote post ───────────────────────────────────────────────────────────────


class TestUnvotePost:
    def test_unvote_decrements_count(self, alice, bob, published_post, db_session):
        VoteService.upvote(bob.id, "post", published_post.id)
        VoteService.unvote(bob.id, "post", published_post.id)
        assert VoteService.vote_count("post", published_post.id) == 0

    def test_unvote_clears_has_voted(self, bob, published_post, db_session):
        VoteService.upvote(bob.id, "post", published_post.id)
        VoteService.unvote(bob.id, "post", published_post.id)
        assert VoteService.has_voted(bob.id, "post", published_post.id) is False

    def test_unvote_reverses_reputation(self, alice, bob, published_post, db_session):
        from backend.extensions import db as _db

        VoteService.upvote(bob.id, "post", published_post.id)
        rep_after_vote = alice.reputation_score
        VoteService.unvote(bob.id, "post", published_post.id)
        _db.session.expire(alice)
        assert alice.reputation_score == rep_after_vote - 1

    def test_unvote_without_vote_raises_404(self, bob, published_post, db_session):
        with pytest.raises(VoteError) as exc_info:
            VoteService.unvote(bob.id, "post", published_post.id)
        assert exc_info.value.status_code == 404

    def test_reputation_never_goes_negative(self, alice, bob, published_post, db_session):
        from backend.extensions import db as _db

        alice.reputation_score = 0
        _db.session.commit()
        VoteService.upvote(bob.id, "post", published_post.id)
        VoteService.unvote(bob.id, "post", published_post.id)
        _db.session.expire(alice)
        assert alice.reputation_score >= 0


# ── vote on comment ───────────────────────────────────────────────────────────


class TestVoteComment:
    def test_upvote_comment(self, alice, bob_comment, db_session):
        # alice votes on bob's comment
        VoteService.upvote(alice.id, "comment", bob_comment.id)
        assert VoteService.vote_count("comment", bob_comment.id) == 1

    def test_comment_vote_no_reputation_change(self, alice, bob, bob_comment, db_session):
        from backend.extensions import db as _db

        before = bob.reputation_score or 0
        VoteService.upvote(alice.id, "comment", bob_comment.id)
        _db.session.expire(bob)
        assert bob.reputation_score == before  # no rep change for comment votes

    def test_self_vote_comment_raises_400(self, bob, bob_comment, db_session):
        with pytest.raises(VoteError) as exc_info:
            VoteService.upvote(bob.id, "comment", bob_comment.id)
        assert exc_info.value.status_code == 400

    def test_vote_nonexistent_comment_raises_404(self, alice, db_session):
        with pytest.raises(VoteError) as exc_info:
            VoteService.upvote(alice.id, "comment", 99999)
        assert exc_info.value.status_code == 404


# ── has_voted / vote_count ────────────────────────────────────────────────────


class TestQueries:
    def test_has_voted_false_by_default(self, bob, published_post, db_session):
        assert VoteService.has_voted(bob.id, "post", published_post.id) is False

    def test_vote_count_zero_by_default(self, published_post, db_session):
        assert VoteService.vote_count("post", published_post.id) == 0

    def test_multiple_voters(self, alice, bob, published_post, db_session):
        from backend.services.auth_service import AuthService

        carol_user = AuthService.register(
            email="carol_vote@example.com", username="carol_vote", password="Password1!"
        )
        VoteService.upvote(bob.id, "post", published_post.id)
        VoteService.upvote(carol_user.id, "post", published_post.id)
        assert VoteService.vote_count("post", published_post.id) == 2
