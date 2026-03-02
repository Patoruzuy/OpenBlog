"""Tests for the tag-follow feature and its integration with notifications.

Covers:
- POST /tags/<slug>/follow  creates a Subscription(target_type='tag')
- POST /tags/<slug>/unfollow  removes the subscription
- Publishing a PUBLIC post with a followed tag notifies the follower
- Publishing a WORKSPACE post with a followed tag does NOT notify the follower
- Unauthenticated follow/unfollow redirects to login
- Duplicate follow is idempotent
"""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.subscription import Subscription
from backend.models.tag import Tag
from backend.services.notification_service import is_subscribed, subscribe, unsubscribe

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@tagfollow.test", "alice_tf")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _ = make_user_token("bob@tagfollow.test", "bob_tf")
    return user


@pytest.fixture()
def python_tag(db_session):
    tag = Tag(name="Python", slug="python")
    db.session.add(tag)
    db.session.commit()
    return tag


@pytest.fixture()
def rust_tag(db_session):
    tag = Tag(name="Rust", slug="rust")
    db.session.add(tag)
    db.session.commit()
    return tag


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# ── SSR follow / unfollow endpoints ──────────────────────────────────────────


class TestTagFollowEndpoints:
    def test_follow_unauthenticated_redirects(self, auth_client, python_tag, db_session):
        resp = auth_client.post(f"/tags/{python_tag.slug}/follow", follow_redirects=False)
        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "login" in resp.headers["Location"]

    def test_unfollow_unauthenticated_redirects(self, auth_client, python_tag, db_session):
        resp = auth_client.post(f"/tags/{python_tag.slug}/unfollow", follow_redirects=False)
        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "login" in resp.headers["Location"]

    def test_follow_creates_subscription(self, auth_client, alice, python_tag, db_session):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            f"/tags/{python_tag.slug}/follow", follow_redirects=True
        )
        assert resp.status_code == 200

        sub = db.session.scalar(
            db.select(Subscription).where(
                Subscription.user_id == alice.id,
                Subscription.target_type == "tag",
                Subscription.target_id == python_tag.id,
            )
        )
        assert sub is not None

    def test_follow_nonexistent_tag_redirects(self, auth_client, alice, db_session):
        _login(auth_client, alice.id)
        resp = auth_client.post("/tags/no-such-tag/follow", follow_redirects=True)
        assert resp.status_code == 200  # Redirected with flash error

    def test_follow_is_idempotent(self, auth_client, alice, python_tag, db_session):
        _login(auth_client, alice.id)
        # Follow twice
        auth_client.post(f"/tags/{python_tag.slug}/follow")
        auth_client.post(f"/tags/{python_tag.slug}/follow")

        count = db.session.scalar(
            db.select(db.func.count(Subscription.id)).where(
                Subscription.user_id == alice.id,
                Subscription.target_type == "tag",
                Subscription.target_id == python_tag.id,
            )
        )
        assert count == 1

    def test_unfollow_removes_subscription(self, auth_client, alice, python_tag, db_session):
        # Set up: follow first via service
        subscribe(alice, "tag", python_tag.id)

        _login(auth_client, alice.id)
        resp = auth_client.post(
            f"/tags/{python_tag.slug}/unfollow", follow_redirects=True
        )
        assert resp.status_code == 200

        assert not is_subscribed(alice, "tag", python_tag.id)

    def test_unfollow_nonexistent_tag_redirects(self, auth_client, alice, db_session):
        _login(auth_client, alice.id)
        resp = auth_client.post("/tags/no-such-tag/unfollow", follow_redirects=True)
        assert resp.status_code == 200

    def test_unfollow_without_prior_follow_is_harmless(
        self, auth_client, alice, python_tag, db_session
    ):
        _login(auth_client, alice.id)
        resp = auth_client.post(
            f"/tags/{python_tag.slug}/unfollow", follow_redirects=True
        )
        assert resp.status_code == 200


# ── Service-level subscription helpers ───────────────────────────────────────


class TestTagSubscriptionService:
    def test_subscribe_creates_row(self, alice, python_tag, db_session):
        sub = subscribe(alice, "tag", python_tag.id)
        assert sub.id is not None
        assert sub.target_type == "tag"
        assert sub.target_id == python_tag.id

    def test_subscribe_idempotent(self, alice, python_tag, db_session):
        sub1 = subscribe(alice, "tag", python_tag.id)
        sub2 = subscribe(alice, "tag", python_tag.id)
        assert sub1.id == sub2.id

    def test_unsubscribe_returns_true(self, alice, python_tag, db_session):
        subscribe(alice, "tag", python_tag.id)
        result = unsubscribe(alice, "tag", python_tag.id)
        assert result is True

    def test_unsubscribe_returns_false_when_not_subscribed(
        self, alice, python_tag, db_session
    ):
        result = unsubscribe(alice, "tag", python_tag.id)
        assert result is False

    def test_is_subscribed_returns_true_after_follow(self, alice, python_tag, db_session):
        subscribe(alice, "tag", python_tag.id)
        assert is_subscribed(alice, "tag", python_tag.id) is True

    def test_is_subscribed_returns_false_after_unfollow(
        self, alice, python_tag, db_session
    ):
        subscribe(alice, "tag", python_tag.id)
        unsubscribe(alice, "tag", python_tag.id)
        assert is_subscribed(alice, "tag", python_tag.id) is False


# ── Notification fanout via get_recipients ────────────────────────────────────


class TestTagFollowNotificationFanout:
    """Test that get_recipients() resolves tag subscribers for post.published events."""

    def test_tag_follower_receives_public_post_notification(
        self, alice, bob, python_tag, db_session
    ):
        """Alice follows 'python'; Bob publishes a public post tagged with 'python'.
        Alice should be in the recipient set."""
        from backend.models.post import Post, PostStatus
        from backend.services.notification_service import get_recipients

        # Alice follows the Python tag
        subscribe(alice, "tag", python_tag.id)

        # Create a public post by Bob tagged with Python
        post = Post(
            title="Python tips",
            slug="python-tips-fanout",
            markdown_body='',
            author_id=bob.id,
            status=PostStatus.published,
            workspace_id=None,  # public
        )
        post.tags = [python_tag]
        db.session.add(post)
        db.session.commit()

        recipients = get_recipients(
            event_type="post.published",
            target_type="post",
            target_id=post.id,
            payload={"tag_ids": [python_tag.id]},
        )

        assert alice.id in recipients

    def test_tag_follower_not_notified_for_workspace_post(
        self, alice, bob, python_tag, db_session
    ):
        """Workspace posts should NOT trigger tag-follower notifications."""
        from backend.models.post import Post, PostStatus
        from backend.models.workspace import Workspace
        from backend.services.notification_service import get_recipients

        # Alice follows the Python tag
        subscribe(alice, "tag", python_tag.id)

        ws = Workspace(name="Private WS", slug="private-ws-tagtest", owner_id=bob.id)
        db.session.add(ws)
        db.session.commit()

        # Workspace post (workspace_id is set)
        post = Post(
            title="Internal Python guide",
            slug="internal-python-guide",
            markdown_body='',
            author_id=bob.id,
            status=PostStatus.published,
            workspace_id=ws.id,  # private workspace
        )
        post.tags = [python_tag]
        db.session.add(post)
        db.session.commit()

        recipients = get_recipients(
            event_type="post.published",
            target_type="post",
            target_id=post.id,
            payload={"tag_ids": [python_tag.id], "workspace_id": ws.id},
        )

        assert alice.id not in recipients

    def test_non_follower_not_in_recipients_for_tag(
        self, alice, bob, python_tag, db_session
    ):
        """A user who did NOT follow the tag should not receive a tag-follow notification."""
        from backend.models.post import Post, PostStatus
        from backend.services.notification_service import get_recipients

        # alice does NOT subscribe

        post = Post(
            title="Python 3.13",
            slug="python-313-nofollow",
            markdown_body='',
            author_id=bob.id,
            status=PostStatus.published,
            workspace_id=None,
        )
        post.tags = [python_tag]
        db.session.add(post)
        db.session.commit()

        recipients = get_recipients(
            event_type="post.published",
            target_type="post",
            target_id=post.id,
            payload={"tag_ids": [python_tag.id]},
        )

        assert alice.id not in recipients

    def test_multiple_tag_followers_all_receive_notification(
        self, alice, bob, python_tag, db_session, make_user_token
    ):
        """All tag followers should receive the notification."""
        from backend.models.post import Post, PostStatus
        from backend.services.notification_service import get_recipients

        charlie, _ = make_user_token("charlie@tagfollow.test", "charlie_tf")

        # Both alice and charlie follow Python
        subscribe(alice, "tag", python_tag.id)
        subscribe(charlie, "tag", python_tag.id)

        post = Post(
            title="Advanced Python",
            slug="advanced-python-multi",
            markdown_body='',
            author_id=bob.id,
            status=PostStatus.published,
            workspace_id=None,
        )
        post.tags = [python_tag]
        db.session.add(post)
        db.session.commit()

        recipients = get_recipients(
            event_type="post.published",
            target_type="post",
            target_id=post.id,
            payload={"tag_ids": [python_tag.id]},
        )

        assert alice.id in recipients
        assert charlie.id in recipients

    def test_only_relevant_tag_followers_notified(
        self, alice, bob, python_tag, rust_tag, db_session
    ):
        """Alice follows only 'rust'; post is tagged 'python' only → alice not notified."""
        from backend.models.post import Post, PostStatus
        from backend.services.notification_service import get_recipients

        subscribe(alice, "tag", rust_tag.id)  # alice follows Rust, not Python

        post = Post(
            title="Pure Python",
            slug="pure-python-tag-filter",
            markdown_body='',
            author_id=bob.id,
            status=PostStatus.published,
            workspace_id=None,
        )
        post.tags = [python_tag]
        db.session.add(post)
        db.session.commit()

        recipients = get_recipients(
            event_type="post.published",
            target_type="post",
            target_id=post.id,
            payload={"tag_ids": [python_tag.id]},
        )

        assert alice.id not in recipients

    def test_tag_id_fallback_from_db(self, alice, bob, python_tag, db_session):
        """When payload has no tag_ids, recipients should be resolved via DB post.tags."""
        from backend.models.post import Post, PostStatus
        from backend.services.notification_service import get_recipients

        subscribe(alice, "tag", python_tag.id)

        post = Post(
            title="DB fallback test",
            slug="db-fallback-tag",
            markdown_body='',
            author_id=bob.id,
            status=PostStatus.published,
            workspace_id=None,
        )
        post.tags = [python_tag]
        db.session.add(post)
        db.session.commit()

        # Payload without tag_ids — service must fall back to post.tags in DB
        recipients = get_recipients(
            event_type="post.published",
            target_type="post",
            target_id=post.id,
            payload={},  # no tag_ids
        )

        assert alice.id in recipients


# ── Tags index page shows follow state ───────────────────────────────────────


class TestTagIndexFollowState:
    def test_authenticated_sees_follow_button(
        self, auth_client, alice, python_tag, db_session
    ):
        _login(auth_client, alice.id)
        resp = auth_client.get("/tags/")
        assert resp.status_code == 200
        assert b"Follow" in resp.data

    def test_following_tag_shows_following_label(
        self, auth_client, alice, python_tag, db_session
    ):
        subscribe(alice, "tag", python_tag.id)
        _login(auth_client, alice.id)
        resp = auth_client.get("/tags/")
        assert resp.status_code == 200
        assert b"Following" in resp.data

    def test_unauthenticated_no_follow_buttons(
        self, auth_client, python_tag, db_session
    ):
        resp = auth_client.get("/tags/")
        assert resp.status_code == 200
        # Follow/unfollow forms should not be present for anonymous users
        # (the template gates them on current_user)
        assert b"/follow" not in resp.data
