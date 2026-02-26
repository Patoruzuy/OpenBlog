"""Unit tests for the SSR notifications inbox (GET /notifications/)."""

from __future__ import annotations

import pytest

from backend.models.notification import Notification

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):  # noqa: ARG001
    user, _tok = make_user_token("alice@notif-ssr.com", "alice_notif")
    return user


@pytest.fixture()
def _alice_notifications(alice, db_session):
    """Two notifications for alice: one read, one unread."""
    from backend.extensions import db

    read_notif = Notification(
        user_id=alice.id,
        notification_type="new_follower",
        title="Bob started following you",
        is_read=True,
    )
    unread_notif = Notification(
        user_id=alice.id,
        notification_type="revision_accepted",
        title="Your revision was accepted",
        body="The editor approved your changes to 'Great Post'.",
        is_read=False,
    )
    db.session.add_all([read_notif, unread_notif])
    db.session.commit()
    return read_notif, unread_notif


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestNotificationsInbox:
    def test_unauthenticated_redirects_to_login(self, auth_client, db_session):
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_authenticated_returns_200(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 200

    def test_content_type_is_html(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert "text/html" in resp.content_type

    def test_empty_state_when_no_notifications(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert b"No notifications yet" in resp.data

    def test_shows_notification_title(self, auth_client, alice, _alice_notifications):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert b"Bob started following you" in resp.data

    def test_shows_notification_body(self, auth_client, alice, _alice_notifications):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert b"approved your changes" in resp.data

    def test_shows_unread_count_badge(self, auth_client, alice, _alice_notifications):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert b"1 unread" in resp.data

    def test_unread_only_filter_shows_only_unread(
        self, auth_client, alice, _alice_notifications
    ):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?unread_only=1")
        assert resp.status_code == 200
        assert b"revision was accepted" in resp.data
        # Read notification should not appear
        assert b"Bob started following you" not in resp.data

    def test_unread_only_empty_state_message(self, auth_client, alice):
        """When filtering unread but there are none, shows appropriate message."""
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?unread_only=1")
        assert b"No unread notifications" in resp.data

    def test_all_filter_shows_both(self, auth_client, alice, _alice_notifications):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert b"Bob started following you" in resp.data
        assert b"revision was accepted" in resp.data

    def test_unread_item_has_unread_css_class(
        self, auth_client, alice, _alice_notifications
    ):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert b"notification-item--unread" in resp.data

    def test_page_param_accepted(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?page=1")
        assert resp.status_code == 200

    def test_invalid_page_clamped(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?page=0")
        assert resp.status_code == 200
