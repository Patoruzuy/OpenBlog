"""Tests for NotificationService."""

from __future__ import annotations

import pytest

from backend.models.notification import Notification
from backend.services.notification_service import NotificationError, NotificationService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@example.com", "alice")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _ = make_user_token("bob@example.com", "bob")
    return user


def _make_notif(user_id: int, *, is_read: bool = False, title: str = "Test") -> Notification:
    from backend.extensions import db

    n = Notification(
        user_id=user_id,
        notification_type="test",
        title=title,
        is_read=is_read,
    )
    db.session.add(n)
    db.session.commit()
    return n


# ── list_for_user ─────────────────────────────────────────────────────────────


class TestListForUser:
    def test_empty(self, alice, db_session):
        notifs, total = NotificationService.list_for_user(alice.id)
        assert total == 0
        assert notifs == []

    def test_returns_all_notifications(self, alice, db_session):
        _make_notif(alice.id, title="A")
        _make_notif(alice.id, title="B", is_read=True)
        notifs, total = NotificationService.list_for_user(alice.id)
        assert total == 2

    def test_unread_only_filter(self, alice, db_session):
        _make_notif(alice.id, title="Unread")
        _make_notif(alice.id, title="Read", is_read=True)
        notifs, total = NotificationService.list_for_user(alice.id, unread_only=True)
        assert total == 1
        assert notifs[0].title == "Unread"

    def test_pagination(self, alice, db_session):
        for i in range(5):
            _make_notif(alice.id, title=f"N{i}")
        notifs, total = NotificationService.list_for_user(alice.id, page=1, per_page=3)
        assert total == 5
        assert len(notifs) == 3

    def test_only_own_notifications(self, alice, bob, db_session):
        _make_notif(bob.id, title="Bob's notif")
        notifs, total = NotificationService.list_for_user(alice.id)
        assert total == 0


# ── mark_read ─────────────────────────────────────────────────────────────────


class TestMarkRead:
    def test_marks_as_read(self, alice, db_session):
        n = _make_notif(alice.id)
        assert n.is_read is False
        result = NotificationService.mark_read(n.id, alice.id)
        assert result.is_read is True
        assert result.read_at is not None

    def test_idempotent_when_already_read(self, alice, db_session):
        n = _make_notif(alice.id, is_read=True)
        result = NotificationService.mark_read(n.id, alice.id)
        assert result.is_read is True  # no error

    def test_wrong_owner_raises_404(self, alice, bob, db_session):
        n = _make_notif(alice.id)
        with pytest.raises(NotificationError) as exc_info:
            NotificationService.mark_read(n.id, bob.id)
        assert exc_info.value.status_code == 404

    def test_nonexistent_raises_404(self, alice, db_session):
        with pytest.raises(NotificationError) as exc_info:
            NotificationService.mark_read(99999, alice.id)
        assert exc_info.value.status_code == 404


# ── mark_all_read ─────────────────────────────────────────────────────────────


class TestMarkAllRead:
    def test_marks_all_unread(self, alice, db_session):
        _make_notif(alice.id)
        _make_notif(alice.id)
        count = NotificationService.mark_all_read(alice.id)
        assert count == 2
        assert NotificationService.unread_count(alice.id) == 0

    def test_skips_already_read(self, alice, db_session):
        _make_notif(alice.id, is_read=True)
        count = NotificationService.mark_all_read(alice.id)
        assert count == 0

    def test_does_not_affect_other_users(self, alice, bob, db_session):
        _make_notif(alice.id)
        NotificationService.mark_all_read(bob.id)
        assert NotificationService.unread_count(alice.id) == 1


# ── unread_count ──────────────────────────────────────────────────────────────


class TestUnreadCount:
    def test_zero_by_default(self, alice, db_session):
        assert NotificationService.unread_count(alice.id) == 0

    def test_counts_unread(self, alice, db_session):
        _make_notif(alice.id)
        _make_notif(alice.id)
        _make_notif(alice.id, is_read=True)
        assert NotificationService.unread_count(alice.id) == 2
