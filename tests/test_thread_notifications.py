"""Tests for the thread notification delivery system.

Covers
------
- NotificationDeliveryService: token round-trip + tamper detection
- NotificationDeliveryService: commenter display name respects privacy mode
- NotificationDeliveryService: markdown strip + excerpt truncation
- NotificationDeliveryService: Redis cooldown set/check
- NotificationDeliveryService.process_comment_created:
    * draft post → no notification
    * author excluded from own notifications
    * subscriber receives in-app notification
    * subscriber receives email when verified and prefs enabled
    * subscriber skipped when email unverified
    * subscriber skipped when on cooldown
    * notify_thread_emails=False → no email
    * reply recipient receives thread_reply in-app notification
    * reply recipient excluded from thread subscriber list (no duplicate email)
    * notify_reply_emails=False → no reply email
- CommentService.create: dispatches notify_thread_comment_created.delay
- GET /threads/<slug>/unsubscribe: valid token, missing token, bad token
"""

from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest

from backend.extensions import db as _db
from backend.models.notification import Notification
from backend.models.portal import IdentityMode, UserPrivacySettings
from backend.models.post import Post, PostStatus
from backend.services.notification_delivery_service import NotificationDeliveryService
from backend.services.thread_subscription_service import ThreadSubscriptionService

# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):  # noqa: ARG001
    user, _ = make_user_token("alice@notif.com", "notif_alice")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):  # noqa: ARG001
    user, _ = make_user_token("bob@notif.com", "notif_bob")
    return user


@pytest.fixture()
def carol(make_user_token, db_session):  # noqa: ARG001
    user, _ = make_user_token("carol@notif.com", "notif_carol")
    return user


@pytest.fixture()
def pub_post(alice, db_session):  # noqa: ARG001
    """Published post authored by alice."""
    post = Post(
        author_id=alice.id,
        title="Test Thread Post",
        slug="test-thread-post",
        markdown_body="# Hello",
        status=PostStatus.published,
    )
    _db.session.add(post)
    _db.session.commit()
    return post


@pytest.fixture()
def draft_post(alice, db_session):  # noqa: ARG001
    """Draft post authored by alice."""
    post = Post(
        author_id=alice.id,
        title="Draft Post",
        slug="draft-post",
        markdown_body="# Draft",
        status=PostStatus.draft,
    )
    _db.session.add(post)
    _db.session.commit()
    return post


@pytest.fixture()
def fake_redis():
    """Isolated fakeredis instance (not the app-level one)."""
    return fakeredis.FakeRedis(decode_responses=True)


def _verify_email(user) -> None:
    """Mark *user* as email-verified and flush."""
    user.is_email_verified = True
    _db.session.commit()


# ── TestUnsubscribeToken ──────────────────────────────────────────────────────


class TestUnsubscribeToken:
    def test_roundtrip(self, app):
        with app.app_context():
            token = NotificationDeliveryService.make_unsubscribe_token(42, 7)
            result = NotificationDeliveryService.verify_unsubscribe_token(token)
            assert result == (42, 7)

    def test_tampered_token_returns_none(self, app):
        with app.app_context():
            token = NotificationDeliveryService.make_unsubscribe_token(1, 2)
            bad = token[:-4] + "XXXX"
            assert NotificationDeliveryService.verify_unsubscribe_token(bad) is None

    def test_empty_token_returns_none(self, app):
        with app.app_context():
            assert NotificationDeliveryService.verify_unsubscribe_token("") is None

    def test_garbage_token_returns_none(self, app):
        with app.app_context():
            assert (
                NotificationDeliveryService.verify_unsubscribe_token("notavalidtoken")
                is None
            )


# ── TestCommenterName ─────────────────────────────────────────────────────────


class TestCommenterName:
    def test_public_uses_display_name(self, db_session, alice):  # noqa: ARG002
        alice.display_name = "Alice Wonderland"
        priv = UserPrivacySettings(
            user_id=alice.id, default_identity_mode=IdentityMode.public.value
        )
        _db.session.add(priv)
        _db.session.flush()
        name = NotificationDeliveryService._get_commenter_name(alice, priv)
        assert name == "Alice Wonderland"

    def test_public_falls_back_to_username(self, db_session, alice):  # noqa: ARG002
        alice.display_name = None
        priv = UserPrivacySettings(
            user_id=alice.id, default_identity_mode=IdentityMode.public.value
        )
        _db.session.add(priv)
        _db.session.flush()
        name = NotificationDeliveryService._get_commenter_name(alice, priv)
        assert name == alice.username

    def test_pseudonymous_uses_alias(self, db_session, alice):  # noqa: ARG002
        priv = UserPrivacySettings(
            user_id=alice.id,
            default_identity_mode=IdentityMode.pseudonymous.value,
            pseudonymous_alias="The Wanderer",
        )
        _db.session.add(priv)
        _db.session.flush()
        name = NotificationDeliveryService._get_commenter_name(alice, priv)
        assert name == "The Wanderer"

    def test_pseudonymous_no_alias_returns_member(self, db_session, alice):  # noqa: ARG002
        priv = UserPrivacySettings(
            user_id=alice.id,
            default_identity_mode=IdentityMode.pseudonymous.value,
            pseudonymous_alias=None,
        )
        _db.session.add(priv)
        _db.session.flush()
        name = NotificationDeliveryService._get_commenter_name(alice, priv)
        assert name == "A member"

    def test_anonymous_returns_someone(self, db_session, alice):  # noqa: ARG002
        priv = UserPrivacySettings(
            user_id=alice.id, default_identity_mode=IdentityMode.anonymous.value
        )
        _db.session.add(priv)
        _db.session.flush()
        name = NotificationDeliveryService._get_commenter_name(alice, priv)
        assert name == "Someone"

    def test_no_privacy_row_returns_public_name(self, db_session, alice):  # noqa: ARG002
        alice.display_name = "Alice Public"
        name = NotificationDeliveryService._get_commenter_name(alice, None)
        assert name == "Alice Public"


# ── TestStripMarkdown ─────────────────────────────────────────────────────────


class TestStripMarkdown:
    def test_strips_asterisks(self):
        assert (
            NotificationDeliveryService._strip_markdown("**bold** text") == "bold text"
        )

    def test_strips_underscores(self):
        result = NotificationDeliveryService._strip_markdown("_italic_")
        assert result == "italic"

    def test_strips_headers(self):
        result = NotificationDeliveryService._strip_markdown("# Heading")
        assert result == "Heading"

    def test_truncates_long_text(self):
        long_text = "a" * 300
        result = NotificationDeliveryService._strip_markdown(long_text, max_len=10)
        assert len(result) <= 11  # 10 chars + ellipsis
        assert result.endswith("\u2026")

    def test_short_text_not_truncated(self):
        text = "short text"
        assert NotificationDeliveryService._strip_markdown(text) == "short text"


# ── TestCooldown ──────────────────────────────────────────────────────────────


class TestCooldown:
    def test_not_on_cooldown_initially(self, app, fake_redis):
        with app.app_context():
            assert (
                NotificationDeliveryService._is_on_cooldown(fake_redis, 1, 1) is False
            )

    def test_on_cooldown_after_set(self, app, fake_redis):
        with app.app_context():
            NotificationDeliveryService._set_cooldown(fake_redis, 1, 1)
            assert NotificationDeliveryService._is_on_cooldown(fake_redis, 1, 1) is True

    def test_cooldown_zero_never_fires(self, app, fake_redis):
        with app.app_context():
            app.config["THREAD_NOTIF_COOLDOWN_SECONDS"] = 0
            try:
                NotificationDeliveryService._set_cooldown(fake_redis, 1, 1)
                assert (
                    NotificationDeliveryService._is_on_cooldown(fake_redis, 1, 1)
                    is False
                )
            finally:
                app.config["THREAD_NOTIF_COOLDOWN_SECONDS"] = 900

    def test_different_users_independent(self, app, fake_redis):
        with app.app_context():
            NotificationDeliveryService._set_cooldown(fake_redis, 1, 1)
            assert (
                NotificationDeliveryService._is_on_cooldown(fake_redis, 2, 1) is False
            )


# ── TestProcessCommentCreated ─────────────────────────────────────────────────


class TestProcessCommentCreated:
    """Integration tests for NotificationDeliveryService.process_comment_created."""

    def _call(self, post, comment_id, author, parent_id=None, body="Great post!"):
        with patch("backend.tasks.email.deliver_email.delay"):
            NotificationDeliveryService.process_comment_created(
                post_id=post.id,
                comment_id=comment_id,
                author_id=author.id,
                parent_id=parent_id,
                body=body,
            )

    # ── draft post ────────────────────────────────────────────────────────

    def test_draft_post_no_notification(self, db_session, alice, bob, draft_post):  # noqa: ARG002
        ThreadSubscriptionService.subscribe(bob.id, draft_post.id)
        _verify_email(bob)
        self._call(draft_post, 999, alice)
        notifs = _db.session.scalars(
            _db.select(Notification).where(Notification.user_id == bob.id)
        ).all()
        assert notifs == []

    # ── author self-exclusion ─────────────────────────────────────────────

    def test_author_not_notified_own_comment(self, db_session, alice, pub_post):  # noqa: ARG002
        ThreadSubscriptionService.subscribe(alice.id, pub_post.id)
        _verify_email(alice)
        self._call(pub_post, 1, alice)
        notifs = _db.session.scalars(
            _db.select(Notification).where(Notification.user_id == alice.id)
        ).all()
        assert notifs == []

    # ── in-app notification ───────────────────────────────────────────────

    def test_subscriber_gets_inapp_notification(self, db_session, alice, bob, pub_post):  # noqa: ARG002
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        self._call(pub_post, 42, alice)
        notifs = _db.session.scalars(
            _db.select(Notification).where(Notification.user_id == bob.id)
        ).all()
        assert len(notifs) == 1
        assert notifs[0].notification_type == "thread_new_comment"

    # ── email verification gate ───────────────────────────────────────────

    def test_email_not_sent_if_unverified(self, db_session, alice, bob, pub_post):  # noqa: ARG002
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        # bob is NOT email-verified (default)
        with patch("backend.tasks.email.deliver_email.delay") as mock_delay:
            NotificationDeliveryService.process_comment_created(
                post_id=pub_post.id,
                comment_id=1,
                author_id=alice.id,
                parent_id=None,
                body="Hello!",
            )
        mock_delay.assert_not_called()

    def test_email_sent_if_verified(self, db_session, alice, bob, pub_post):  # noqa: ARG002
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        _verify_email(bob)
        with patch("backend.tasks.email.deliver_email.delay") as mock_delay:
            NotificationDeliveryService.process_comment_created(
                post_id=pub_post.id,
                comment_id=1,
                author_id=alice.id,
                parent_id=None,
                body="Hello!",
            )
        mock_delay.assert_called_once()

    # ── cooldown ──────────────────────────────────────────────────────────

    def test_email_skipped_on_cooldown(self, db_session, app, alice, bob, pub_post):  # noqa: ARG002
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        _verify_email(bob)

        fake_r = fakeredis.FakeRedis(decode_responses=True)
        original_redis = app.extensions.get("redis")
        app.extensions["redis"] = fake_r
        try:
            with patch("backend.tasks.email.deliver_email.delay") as mock_delay:
                # First comment establishes cooldown
                NotificationDeliveryService.process_comment_created(
                    post_id=pub_post.id,
                    comment_id=1,
                    author_id=alice.id,
                    parent_id=None,
                    body="First comment",
                )
                first_calls = mock_delay.call_count

                # Second comment within cooldown window
                NotificationDeliveryService.process_comment_created(
                    post_id=pub_post.id,
                    comment_id=2,
                    author_id=alice.id,
                    parent_id=None,
                    body="Second comment",
                )
                second_calls = mock_delay.call_count
        finally:
            if original_redis is not None:
                app.extensions["redis"] = original_redis

        assert first_calls == 1  # first email was sent
        assert second_calls == 1  # second email was NOT sent (cooldown)

    # ── notify_thread_emails=False ────────────────────────────────────────

    def test_no_email_when_pref_disabled(self, db_session, alice, bob, pub_post):  # noqa: ARG002
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        _verify_email(bob)
        priv = UserPrivacySettings(
            user_id=bob.id,
            notify_thread_emails=False,
            notify_reply_emails=True,
        )
        _db.session.add(priv)
        _db.session.commit()

        with patch("backend.tasks.email.deliver_email.delay") as mock_delay:
            NotificationDeliveryService.process_comment_created(
                post_id=pub_post.id,
                comment_id=1,
                author_id=alice.id,
                parent_id=None,
                body="Hello!",
            )
        mock_delay.assert_not_called()

    # ── reply-to-you ──────────────────────────────────────────────────────

    def test_reply_recipient_gets_thread_reply_notification(
        self,
        db_session,
        alice,
        bob,
        carol,
        pub_post,  # noqa: ARG002
    ):
        from backend.models.comment import Comment  # noqa: PLC0415

        parent = Comment(post_id=pub_post.id, author_id=bob.id, body="Bob's comment")
        _db.session.add(parent)
        _db.session.commit()

        self._call(pub_post, 99, carol, parent_id=parent.id, body="reply body")

        notifs = _db.session.scalars(
            _db.select(Notification).where(Notification.user_id == bob.id)
        ).all()
        types = [n.notification_type for n in notifs]
        assert "thread_reply" in types

    def test_reply_recipient_excluded_from_thread_subscriber_list(
        self,
        db_session,
        alice,
        bob,
        carol,
        pub_post,  # noqa: ARG002
    ):
        """Bob subscribed to thread AND alice replies to Bob → Bob gets only the
        reply email, not a duplicate thread-subscriber email."""
        from backend.models.comment import Comment  # noqa: PLC0415

        parent = Comment(post_id=pub_post.id, author_id=bob.id, body="Bob top-level")
        _db.session.add(parent)
        _db.session.commit()

        # Bob subscribes to the thread
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        _verify_email(bob)

        with patch("backend.tasks.email.deliver_email.delay") as mock_delay:
            NotificationDeliveryService.process_comment_created(
                post_id=pub_post.id,
                comment_id=88,
                author_id=alice.id,
                parent_id=parent.id,
                body="Reply from alice to bob",
            )

        # Only one email call total (reply email, not thread+reply)
        assert mock_delay.call_count == 1
        # Check it's the reply template
        _, call_kwargs = mock_delay.call_args
        # deliver_email.delay(log_id, to_email, subject, template_key, context, locale)
        call_args = mock_delay.call_args[0]
        assert call_args[3] == "thread_reply_to_you"

    def test_no_reply_email_when_pref_disabled(
        self,
        db_session,
        alice,
        bob,
        carol,
        pub_post,  # noqa: ARG002
    ):
        from backend.models.comment import Comment  # noqa: PLC0415

        parent = Comment(post_id=pub_post.id, author_id=bob.id, body="Bob's comment")
        _db.session.add(parent)
        _db.session.commit()

        _verify_email(bob)
        priv = UserPrivacySettings(
            user_id=bob.id,
            notify_thread_emails=True,
            notify_reply_emails=False,
        )
        _db.session.add(priv)
        _db.session.commit()

        with patch("backend.tasks.email.deliver_email.delay") as mock_delay:
            NotificationDeliveryService.process_comment_created(
                post_id=pub_post.id,
                comment_id=55,
                author_id=carol.id,
                parent_id=parent.id,
                body="Carol replies to bob",
            )
        mock_delay.assert_not_called()

    def test_no_self_reply_notification(self, db_session, alice, pub_post):  # noqa: ARG002
        """Author replying to their own comment does not generate a notification."""
        from backend.models.comment import Comment  # noqa: PLC0415

        parent = Comment(
            post_id=pub_post.id, author_id=alice.id, body="Alice top-level"
        )
        _db.session.add(parent)
        _db.session.commit()

        self._call(pub_post, 200, alice, parent_id=parent.id, body="Alice self-reply")

        notifs = _db.session.scalars(
            _db.select(Notification).where(Notification.user_id == alice.id)
        ).all()
        assert notifs == []

    # ── multiple subscribers ──────────────────────────────────────────────

    def test_multiple_subscribers_all_notified(
        self,
        db_session,
        alice,
        bob,
        carol,
        pub_post,  # noqa: ARG002
    ):
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        ThreadSubscriptionService.subscribe(carol.id, pub_post.id)

        self._call(pub_post, 10, alice)

        bob_notifs = _db.session.scalars(
            _db.select(Notification).where(Notification.user_id == bob.id)
        ).all()
        carol_notifs = _db.session.scalars(
            _db.select(Notification).where(Notification.user_id == carol.id)
        ).all()
        assert len(bob_notifs) == 1
        assert len(carol_notifs) == 1


# ── TestCommentServiceDispatch ────────────────────────────────────────────────


class TestCommentServiceDispatch:
    """Verify that CommentService.create fires the Celery task."""

    def test_create_dispatches_notification_task(self, db_session, alice, pub_post):  # noqa: ARG002
        from backend.services.comment_service import CommentService  # noqa: PLC0415

        with patch(
            "backend.tasks.notifications.notify_thread_comment_created.delay"
        ) as mock_delay:
            comment = CommentService.create(pub_post.id, alice.id, "Hello thread!")

        mock_delay.assert_called_once()
        payload = mock_delay.call_args[0][0]
        assert payload["post_id"] == pub_post.id
        assert payload["comment_id"] == comment.id
        assert payload["author_id"] == alice.id
        assert payload["parent_id"] is None
        assert payload["body"] == "Hello thread!"

    def test_reply_dispatch_includes_parent_id(self, db_session, alice, bob, pub_post):  # noqa: ARG002
        from backend.models.comment import Comment  # noqa: PLC0415
        from backend.services.comment_service import CommentService  # noqa: PLC0415

        parent = Comment(post_id=pub_post.id, author_id=bob.id, body="parent")
        _db.session.add(parent)
        _db.session.commit()

        with patch(
            "backend.tasks.notifications.notify_thread_comment_created.delay"
        ) as mock_delay:
            CommentService.create(pub_post.id, alice.id, "Reply!", parent_id=parent.id)

        payload = mock_delay.call_args[0][0]
        assert payload["parent_id"] == parent.id


# ── TestUnsubscribeRoute ──────────────────────────────────────────────────────


class TestUnsubscribeRoute:
    def test_missing_token_returns_400(self, auth_client, db_session, alice, pub_post):  # noqa: ARG002
        resp = auth_client.get(f"/threads/{pub_post.slug}/unsubscribe")
        assert resp.status_code == 400

    def test_bad_token_returns_400(self, auth_client, db_session, alice, pub_post):  # noqa: ARG002
        resp = auth_client.get(f"/threads/{pub_post.slug}/unsubscribe?token=tampered")
        assert resp.status_code == 400

    def test_valid_token_unsubscribes_and_returns_200(
        self,
        app,
        auth_client,
        db_session,
        alice,
        bob,
        pub_post,  # noqa: ARG002
    ):
        ThreadSubscriptionService.subscribe(bob.id, pub_post.id)
        assert ThreadSubscriptionService.is_subscribed(bob.id, pub_post.id) is True

        with app.app_context():
            token = NotificationDeliveryService.make_unsubscribe_token(
                bob.id, pub_post.id
            )

        resp = auth_client.get(
            f"/threads/{pub_post.slug}/unsubscribe?token={token}",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert not ThreadSubscriptionService.is_subscribed(bob.id, pub_post.id)

    def test_unsubscribe_idempotent(
        self,
        app,
        auth_client,
        db_session,
        alice,
        bob,
        pub_post,  # noqa: ARG002
    ):
        """Calling unsubscribe when not subscribed still returns 200."""
        with app.app_context():
            token = NotificationDeliveryService.make_unsubscribe_token(
                bob.id, pub_post.id
            )

        resp = auth_client.get(
            f"/threads/{pub_post.slug}/unsubscribe?token={token}",
            follow_redirects=True,
        )
        assert resp.status_code == 200
