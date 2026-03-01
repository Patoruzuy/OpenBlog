"""Tests for the notification API endpoints."""

from __future__ import annotations

import pytest

from backend.models.notification import Notification

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, tok = make_user_token("alice@example.com", "alice")
    return user, tok


@pytest.fixture()
def bob(make_user_token, db_session):
    user, tok = make_user_token("bob@example.com", "bob")
    return user, tok


def _notif(user_id: int, *, is_read: bool = False, title: str = "Test") -> Notification:
    from backend.extensions import db

    n = Notification(
        user_id=user_id,
        notification_type="test_type",
        title=title,
        is_read=is_read,
    )
    db.session.add(n)
    db.session.commit()
    return n


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── GET /api/notifications/ ───────────────────────────────────────────────────


class TestListNotifications:
    def test_empty_returns_200(self, auth_client, alice, db_session):
        _, tok = alice
        resp = auth_client.get("/api/notifications/", headers=_h(tok))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["notifications"] == []
        assert data["unread_count"] == 0

    def test_returns_own_notifications(self, auth_client, alice, db_session):
        user, tok = alice
        _notif(user.id, title="Hello")
        resp = auth_client.get("/api/notifications/", headers=_h(tok))
        data = resp.get_json()
        assert data["total"] == 1
        assert data["notifications"][0]["title"] == "Hello"

    def test_unread_only_filter(self, auth_client, alice, db_session):
        user, tok = alice
        _notif(user.id, title="Unread")
        _notif(user.id, title="Read", is_read=True)
        resp = auth_client.get("/api/notifications/?unread_only=true", headers=_h(tok))
        data = resp.get_json()
        assert data["total"] == 1
        assert data["notifications"][0]["title"] == "Unread"

    def test_pagination(self, auth_client, alice, db_session):
        user, tok = alice
        for i in range(5):
            _notif(user.id, title=f"N{i}")
        resp = auth_client.get("/api/notifications/?page=1&per_page=3", headers=_h(tok))
        data = resp.get_json()
        assert data["total"] == 5
        assert len(data["notifications"]) == 3
        assert data["pages"] == 2

    def test_requires_auth(self, auth_client, db_session):
        resp = auth_client.get("/api/notifications/")
        assert resp.status_code == 401

    def test_does_not_return_other_users_notifications(
        self, auth_client, alice, bob, db_session
    ):
        bob_user, _ = bob
        _, alice_tok = alice
        _notif(bob_user.id, title="Bob's notif")
        resp = auth_client.get("/api/notifications/", headers=_h(alice_tok))
        assert resp.get_json()["total"] == 0


# ── GET /api/notifications/unread-count ───────────────────────────────────────


class TestUnreadCount:
    def test_zero_by_default(self, auth_client, alice, db_session):
        _, tok = alice
        resp = auth_client.get("/api/notifications/unread-count", headers=_h(tok))
        assert resp.status_code == 200
        assert resp.get_json()["unread_count"] == 0

    def test_counts_unread(self, auth_client, alice, db_session):
        user, tok = alice
        _notif(user.id)
        _notif(user.id)
        _notif(user.id, is_read=True)
        resp = auth_client.get("/api/notifications/unread-count", headers=_h(tok))
        assert resp.get_json()["unread_count"] == 2

    def test_requires_auth(self, auth_client, db_session):
        resp = auth_client.get("/api/notifications/unread-count")
        assert resp.status_code == 401


# ── POST /api/notifications/<id>/read ────────────────────────────────────────


class TestMarkRead:
    def test_marks_as_read(self, auth_client, alice, db_session):
        user, tok = alice
        n = _notif(user.id)
        resp = auth_client.post(f"/api/notifications/{n.id}/read", headers=_h(tok))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_read"] is True
        assert data["read_at"] is not None

    def test_wrong_owner_returns_404(self, auth_client, alice, bob, db_session):
        bob_user, _ = bob
        _, alice_tok = alice
        n = _notif(bob_user.id)
        resp = auth_client.post(
            f"/api/notifications/{n.id}/read", headers=_h(alice_tok)
        )
        assert resp.status_code == 404

    def test_nonexistent_returns_404(self, auth_client, alice, db_session):
        _, tok = alice
        resp = auth_client.post("/api/notifications/99999/read", headers=_h(tok))
        assert resp.status_code == 404

    def test_requires_auth(self, auth_client, alice, db_session):
        user, _ = alice
        n = _notif(user.id)
        resp = auth_client.post(f"/api/notifications/{n.id}/read")
        assert resp.status_code == 401


# ── POST /api/notifications/read-all ─────────────────────────────────────────


class TestMarkAllRead:
    def test_marks_all_unread(self, auth_client, alice, db_session):
        user, tok = alice
        _notif(user.id)
        _notif(user.id)
        resp = auth_client.post("/api/notifications/read-all", headers=_h(tok))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["marked_read"] == 2
        assert data["unread_count"] == 0

    def test_zero_when_all_read(self, auth_client, alice, db_session):
        user, tok = alice
        _notif(user.id, is_read=True)
        resp = auth_client.post("/api/notifications/read-all", headers=_h(tok))
        assert resp.get_json()["marked_read"] == 0

    def test_requires_auth(self, auth_client, db_session):
        resp = auth_client.post("/api/notifications/read-all")
        assert resp.status_code == 401

    def test_does_not_affect_other_users(self, auth_client, alice, bob, db_session):
        alice_user, _ = alice
        _, bob_tok = bob
        _notif(alice_user.id)
        resp = auth_client.post("/api/notifications/read-all", headers=_h(bob_tok))
        assert resp.get_json()["marked_read"] == 0
        from backend.services.notification_service import NotificationService

        assert NotificationService.unread_count(alice_user.id) == 1
