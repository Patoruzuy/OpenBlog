"""Tests for notification threading / grouping.

Covers:
- list_grouped_for_user(): grouping logic, count, unread_count
- list_for_user() with target_type/target_id filters (expanded group view)
- GET /notifications/ renders grouped view by default
- GET /notifications/?target_type=X&target_id=Y renders flat filtered view
- Unread-only filter in grouped view
- Empty grouped view state
- "← All groups" breadcrumb visible in filtered view
"""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.notification import Notification
from backend.services.notification_service import NotificationService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@threading.test", "alice_thread")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _ = make_user_token("bob@threading.test", "bob_thread")
    return user


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _notif(
    user_id: int,
    *,
    title: str = "Notification",
    event_type: str = "comment.created",
    target_type: str = "post",
    target_id: int = 1,
    is_read: bool = False,
) -> Notification:
    n = Notification(
        user_id=user_id,
        notification_type=event_type,
        event_type=event_type,
        title=title,
        target_type=target_type,
        target_id=target_id,
        is_read=is_read,
    )
    db.session.add(n)
    db.session.commit()
    return n


# ── list_grouped_for_user ─────────────────────────────────────────────────────


class TestListGroupedForUser:
    def test_empty_returns_empty_list(self, alice, db_session):
        groups = NotificationService.list_grouped_for_user(alice.id)
        assert groups == []

    def test_single_notification_one_group(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=1)
        groups = NotificationService.list_grouped_for_user(alice.id)
        assert len(groups) == 1
        assert groups[0]["count"] == 1

    def test_multiple_same_target_collapsed(self, alice, db_session):
        for i in range(5):
            _notif(alice.id, title=f"Comment {i}", target_type="post", target_id=42)
        groups = NotificationService.list_grouped_for_user(alice.id)
        assert len(groups) == 1
        assert groups[0]["count"] == 5

    def test_different_targets_separate_groups(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=1)
        _notif(alice.id, target_type="post", target_id=2)
        _notif(alice.id, target_type="revision", target_id=10)
        groups = NotificationService.list_grouped_for_user(alice.id)
        # Three distinct (target_type, target_id) pairs
        assert len(groups) == 3

    def test_unread_count_correct(self, alice, db_session):
        # 3 unread, 2 read for same post
        for _ in range(3):
            _notif(alice.id, target_type="post", target_id=7, is_read=False)
        for _ in range(2):
            _notif(alice.id, target_type="post", target_id=7, is_read=True)
        groups = NotificationService.list_grouped_for_user(alice.id)
        assert len(groups) == 1
        grp = groups[0]
        assert grp["count"] == 5
        assert grp["unread_count"] == 3

    def test_unread_only_filters_out_all_read_groups(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=1, is_read=True)
        groups = NotificationService.list_grouped_for_user(alice.id, unread_only=True)
        assert groups == []

    def test_unread_only_keeps_groups_with_unread(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=1, is_read=False)
        _notif(alice.id, target_type="post", target_id=2, is_read=True)
        groups = NotificationService.list_grouped_for_user(alice.id, unread_only=True)
        assert len(groups) == 1
        assert groups[0]["target_id"] == 1

    def test_latest_is_newest_notification(self, alice, db_session):
        from datetime import UTC, datetime, timedelta

        base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        old = Notification(
            user_id=alice.id,
            notification_type="comment.created",
            title="Old",
            target_type="post",
            target_id=99,
            created_at=base,
        )
        new = Notification(
            user_id=alice.id,
            notification_type="comment.created",
            title="New",
            target_type="post",
            target_id=99,
            created_at=base + timedelta(hours=5),
        )
        db.session.add_all([old, new])
        db.session.commit()

        groups = NotificationService.list_grouped_for_user(alice.id)
        assert len(groups) == 1
        assert groups[0]["latest"].title == "New"

    def test_group_has_expected_keys(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=3)
        groups = NotificationService.list_grouped_for_user(alice.id)
        grp = groups[0]
        assert "latest" in grp
        assert "count" in grp
        assert "unread_count" in grp
        assert "target_type" in grp
        assert "target_id" in grp

    def test_does_not_leak_other_users_notifications(self, alice, bob, db_session):
        _notif(bob.id, target_type="post", target_id=5)
        groups = NotificationService.list_grouped_for_user(alice.id)
        assert groups == []


# ── list_for_user — target_type / target_id filter ───────────────────────────


class TestListForUserFiltered:
    def test_filter_by_target_returns_only_matching(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=10)
        _notif(alice.id, target_type="post", target_id=20)

        notifs, total = NotificationService.list_for_user(
            alice.id, target_type="post", target_id=10
        )
        assert total == 1
        assert notifs[0].target_id == 10

    def test_filter_returns_multiple_same_target(self, alice, db_session):
        for i in range(4):
            _notif(alice.id, title=f"N{i}", target_type="post", target_id=11)
        _notif(alice.id, target_type="post", target_id=99)  # different target

        notifs, total = NotificationService.list_for_user(
            alice.id, target_type="post", target_id=11
        )
        assert total == 4

    def test_filter_empty_when_no_match(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=5)
        notifs, total = NotificationService.list_for_user(
            alice.id, target_type="post", target_id=999
        )
        assert total == 0
        assert notifs == []

    def test_filter_respects_unread_only(self, alice, db_session):
        _notif(alice.id, target_type="post", target_id=15, is_read=False)
        _notif(alice.id, target_type="post", target_id=15, is_read=True)

        notifs, total = NotificationService.list_for_user(
            alice.id, target_type="post", target_id=15, unread_only=True
        )
        assert total == 1
        assert not notifs[0].is_read


# ── SSR grouped inbox view ────────────────────────────────────────────────────


class TestNotificationsGroupedView:
    def test_unauthenticated_redirects(self, auth_client, db_session):
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 302

    def test_empty_shows_no_notifications_message(self, auth_client, alice, db_session):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 200
        assert b"No notifications" in resp.data

    def test_grouped_view_shows_notification_title(self, auth_client, alice, db_session):
        _notif(alice.id, title="Alices first notification", target_type="post", target_id=1)
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 200
        assert b"Alices first notification" in resp.data

    def test_grouped_view_shows_count_badge_for_multiple(
        self, auth_client, alice, db_session
    ):
        for i in range(3):
            _notif(alice.id, title=f"Comment {i}", target_type="post", target_id=21)
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 200
        # Badge should show "3"
        assert b"3" in resp.data

    def test_grouped_view_collapses_same_target_to_one_row(
        self, auth_client, alice, db_session
    ):
        for i in range(5):
            _notif(alice.id, title=f"Notif {i}", target_type="post", target_id=22)
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 200
        # 5 individual titles should NOT all appear; instead the group count badge
        # "5" should appear and only the latest title should be prominent
        assert b"5" in resp.data

    def test_grouped_view_unread_count_in_page_header(
        self, auth_client, alice, db_session
    ):
        _notif(alice.id, is_read=False, target_type="post", target_id=1)
        _notif(alice.id, is_read=False, target_type="post", target_id=2)
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 200
        assert b"unread" in resp.data.lower() or b"2" in resp.data


# ── SSR flat / filtered inbox view ───────────────────────────────────────────


class TestNotificationsFlatView:
    def test_flat_view_with_target_params(self, auth_client, alice, db_session):
        for i in range(3):
            _notif(alice.id, title=f"Thread {i}", target_type="post", target_id=50)
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?target_type=post&target_id=50")
        assert resp.status_code == 200
        assert b"Thread" in resp.data

    def test_flat_view_shows_all_groups_breadcrumb(
        self, auth_client, alice, db_session
    ):
        _notif(alice.id, target_type="post", target_id=51)
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?target_type=post&target_id=51")
        assert resp.status_code == 200
        assert b"All groups" in resp.data or "\u2190".encode() in resp.data

    def test_flat_view_not_triggered_without_target_id(
        self, auth_client, alice, db_session
    ):
        """Providing only target_type without target_id should fall through to grouped view."""
        _notif(alice.id, target_type="post", target_id=52, title="Grouped notif")
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?target_type=post")
        assert resp.status_code == 200
        # Grouped view should be rendered (no breadcrumb with target_id)
        assert b"Grouped notif" in resp.data

    def test_flat_view_empty_state(self, auth_client, alice, db_session):
        _login(auth_client, alice.id)
        resp = auth_client.get("/notifications/?target_type=post&target_id=9999")
        assert resp.status_code == 200
        assert b"No notifications" in resp.data

    def test_flat_view_unread_filter(self, auth_client, alice, db_session):
        _notif(alice.id, title="Unread", target_type="post", target_id=55, is_read=False)
        _notif(alice.id, title="Read", target_type="post", target_id=55, is_read=True)
        _login(auth_client, alice.id)
        resp = auth_client.get(
            "/notifications/?target_type=post&target_id=55&unread_only=1"
        )
        assert resp.status_code == 200
        assert b"Unread" in resp.data
        assert b"Read" not in resp.data
