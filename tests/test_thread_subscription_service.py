"""Tests for ThreadSubscriptionService."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.services.thread_subscription_service import ThreadSubscriptionService

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
def carol(make_user_token, db_session):
    user, _ = make_user_token("carol@example.com", "carol")
    return user


@pytest.fixture()
def pub_post(alice, db_session):
    from backend.extensions import db

    post = Post(
        author_id=alice.id,
        title="Thread Post",
        slug="thread-post",
        markdown_body="# Thread",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── subscribe ─────────────────────────────────────────────────────────────────


class TestSubscribe:
    def test_subscribe_persists(self, bob, pub_post, db_session):
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        assert ThreadSubscriptionService.is_subscribed(bob.id, pub_post.id) is True

    def test_subscribe_idempotent(self, bob, pub_post, db_session):
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        # second call should not raise
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        assert ThreadSubscriptionService.is_subscribed(bob.id, pub_post.id) is True

    def test_not_subscribed_by_default(self, bob, pub_post, db_session):
        assert ThreadSubscriptionService.is_subscribed(bob.id, pub_post.id) is False


# ── unsubscribe ───────────────────────────────────────────────────────────────


class TestUnsubscribe:
    def test_unsubscribe_removes_subscription(self, bob, pub_post, db_session):
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        ThreadSubscriptionService.unsubscribe(bob.id, pub_post.id)
        assert ThreadSubscriptionService.is_subscribed(bob.id, pub_post.id) is False

    def test_unsubscribe_no_op_if_not_subscribed(self, bob, pub_post, db_session):
        # Should not raise
        ThreadSubscriptionService.unsubscribe(bob.id, pub_post.id)


# ── get_subscribers ───────────────────────────────────────────────────────────


class TestGetSubscribers:
    def test_returns_all_subscriber_ids(self, alice, bob, carol, pub_post, db_session):
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        ThreadSubscriptionService.subscribe(carol.id, pub_post.id)
        subs = ThreadSubscriptionService.get_subscribers(pub_post.id)
        assert set(subs) == {bob.id, carol.id}

    def test_returns_empty_when_no_subscribers(self, pub_post, db_session):
        assert ThreadSubscriptionService.get_subscribers(pub_post.id) == []

    def test_unsubscribed_user_not_in_list(self, bob, carol, pub_post, db_session):
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        ThreadSubscriptionService.subscribe(carol.id, pub_post.id)
        ThreadSubscriptionService.unsubscribe(bob.id, pub_post.id)
        subs = ThreadSubscriptionService.get_subscribers(pub_post.id)
        assert bob.id not in subs
        assert carol.id in subs
