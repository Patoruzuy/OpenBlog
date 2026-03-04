"""Unit tests for the email digest system.

Covers:
- period_key / period_window helpers
- build_digest_for_user (grouping, access filtering)
- record_digest_run (upsert / idempotency)
- send_digest_for_user (happy path, no-notifications skip, idempotency guard)
- Celery fan-out tasks (send_daily_digests, send_weekly_digests)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.extensions import db
from backend.models.digest_run import DigestRun
from backend.models.notification import Notification
from backend.models.notification_preference import NotificationPreference
from backend.services.digest_service import (
    build_digest_for_user,
    period_key,
    period_window,
    record_digest_run,
    send_digest_for_user,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):  # noqa: ARG001
    user, _ = make_user_token("alice@digest.test", "alice_digest")
    return user


@pytest.fixture()
def alice_pref(alice, db_session):
    """Ensure alice has a NotificationPreference row with daily digest enabled."""
    pref = db.session.get(NotificationPreference, alice.id)
    if pref is None:
        pref = NotificationPreference(user_id=alice.id)
        db.session.add(pref)
    pref.email_enabled = True
    pref.email_digest_frequency = "daily"
    db.session.commit()
    return pref


def _notif(
    user_id: int,
    *,
    title: str = "Test notification",
    event_type: str = "revision.accepted",
    target_type: str = "tag",
    target_id: int = 1,
    created_at: datetime | None = None,
) -> Notification:
    n = Notification(
        user_id=user_id,
        notification_type=event_type,
        event_type=event_type,
        title=title,
        target_type=target_type,
        target_id=target_id,
        created_at=created_at or datetime.now(UTC),
    )
    db.session.add(n)
    db.session.commit()
    return n


# ── period_key ────────────────────────────────────────────────────────────────


class TestPeriodKey:
    def test_daily_format(self):
        dt = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
        assert period_key("daily", dt) == "2026-03-02"

    def test_weekly_format(self):
        # 2026-03-02 is a Monday in ISO week 10
        dt = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
        assert period_key("weekly", dt) == "2026-W10"

    def test_weekly_mid_week(self):
        # 2026-03-04 is Wednesday — still week 10
        dt = datetime(2026, 3, 4, 14, 0, tzinfo=UTC)
        assert period_key("weekly", dt) == "2026-W10"

    def test_weekly_sunday(self):
        # ISO week: Sunday belongs to the same week as the preceding Monday
        dt = datetime(2026, 3, 8, 23, 59, tzinfo=UTC)
        assert period_key("weekly", dt) == "2026-W10"

    def test_unknown_frequency_raises(self):
        with pytest.raises(ValueError, match="Unknown frequency"):
            period_key("monthly", datetime.now(UTC))


# ── period_window ─────────────────────────────────────────────────────────────


class TestPeriodWindow:
    def test_daily_round_trip(self):
        dt = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
        key = period_key("daily", dt)
        start, end = period_window("daily", key)
        assert start == datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 3, 3, 0, 0, tzinfo=UTC)
        # span is exactly 1 day
        assert end - start == timedelta(days=1)

    def test_weekly_round_trip(self):
        dt = datetime(2026, 3, 4, 9, 0, tzinfo=UTC)
        key = period_key("weekly", dt)
        start, end = period_window("weekly", key)
        # start is always a Monday
        assert start.weekday() == 0
        assert end - start == timedelta(weeks=1)

    def test_daily_window_contains_noon(self):
        key = "2026-06-15"
        start, end = period_window("daily", key)
        noon = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        assert start <= noon < end

    def test_daily_window_excludes_next_day(self):
        key = "2026-06-15"
        start, end = period_window("daily", key)
        next_midnight = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
        assert next_midnight >= end

    def test_unknown_frequency_raises(self):
        with pytest.raises(ValueError, match="Unknown frequency"):
            period_window("monthly", "2026-03-02")


# ── build_digest_for_user ─────────────────────────────────────────────────────


class TestBuildDigestForUser:
    def test_returns_none_when_no_notifications(self, alice, db_session):  # noqa: ARG001
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 2, tzinfo=UTC)
        result = build_digest_for_user(alice, since, until)
        assert result is None

    def test_returns_none_when_notifications_outside_window(self, alice, db_session):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 2, tzinfo=UTC)
        _notif(alice.id, created_at=datetime(2026, 1, 3, tzinfo=UTC))  # outside
        result = build_digest_for_user(alice, since, until)
        assert result is None

    def test_groups_single_notification(self, alice, db_session):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 2, tzinfo=UTC)
        _notif(
            alice.id,
            event_type="revision.accepted",
            target_type="tag",
            target_id=99,
            created_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
        data = build_digest_for_user(alice, since, until)
        assert data is not None
        assert data.total_count == 1
        assert len(data.groups) == 1
        assert data.groups[0].count == 1

    def test_groups_same_key_together(self, alice, db_session):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 2, tzinfo=UTC)
        for i in range(3):
            _notif(
                alice.id,
                event_type="comment.created",
                target_type="tag",
                target_id=7,
                title=f"Comment {i}",
                created_at=datetime(2026, 1, 1, 10 + i, 0, tzinfo=UTC),
            )
        data = build_digest_for_user(alice, since, until)
        assert data is not None
        assert data.total_count == 3
        # All three collapse into one group (same event+target)
        assert len(data.groups) == 1
        assert data.groups[0].count == 3

    def test_different_events_produce_separate_groups(self, alice, db_session):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 2, tzinfo=UTC)
        _notif(
            alice.id,
            event_type="revision.accepted",
            target_type="tag",
            target_id=5,
            created_at=datetime(2026, 1, 1, 9, tzinfo=UTC),
        )
        _notif(
            alice.id,
            event_type="comment.created",
            target_type="tag",
            target_id=5,
            created_at=datetime(2026, 1, 1, 10, tzinfo=UTC),
        )
        data = build_digest_for_user(alice, since, until)
        assert data is not None
        assert len(data.groups) == 2

    def test_period_label_daily(self, alice, db_session):
        since = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
        until = datetime(2026, 3, 3, 0, 0, tzinfo=UTC)
        _notif(alice.id, created_at=datetime(2026, 3, 2, 10, tzinfo=UTC))
        data = build_digest_for_user(alice, since, until, frequency="daily")
        assert data is not None
        assert "March 02" in data.period_label or "Mar 02" in data.period_label

    def test_period_label_weekly(self, alice, db_session):
        since = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
        until = datetime(2026, 3, 9, 0, 0, tzinfo=UTC)
        _notif(alice.id, created_at=datetime(2026, 3, 4, 10, tzinfo=UTC))
        data = build_digest_for_user(alice, since, until, frequency="weekly")
        assert data is not None
        # label should contain both start and end date markers
        assert "2026" in data.period_label

    def test_workspace_notification_excluded_when_not_member(self, alice, db_session):
        """Workspace-scoped notifications are excluded unless user is a member."""
        from backend.models.workspace import Workspace

        ws = Workspace(name="Private WS", slug="private-ws-digest", owner_id=alice.id)
        db.session.add(ws)
        db.session.commit()

        # Notification targeting the workspace (no membership)
        n = Notification(
            user_id=alice.id,
            notification_type="workspace_invite",
            event_type="revision.accepted",
            title="WS notification",
            target_type="workspace",
            target_id=ws.id,
            created_at=datetime(2026, 1, 1, 10, tzinfo=UTC),
        )
        db.session.add(n)
        db.session.commit()

        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 2, tzinfo=UTC)
        data = build_digest_for_user(alice, since, until)
        # Excluded because alice is not a member
        assert data is None


# ── record_digest_run ─────────────────────────────────────────────────────────


class TestRecordDigestRun:
    def test_inserts_new_row(self, alice, db_session):
        since, until = period_window("daily", "2026-01-01")
        run = record_digest_run(
            user_id=alice.id,
            frequency="daily",
            pkey="2026-01-01",
            period_start=since,
            period_end=until,
            count=3,
            status="sent",
        )
        assert run.id is not None
        assert run.status == "sent"
        assert run.notification_count == 3

    def test_upsert_updates_existing_row(self, alice, db_session):
        since, until = period_window("daily", "2026-01-02")
        run1 = record_digest_run(
            user_id=alice.id,
            frequency="daily",
            pkey="2026-01-02",
            period_start=since,
            period_end=until,
            count=2,
            status="failed",
            error_message="SMTP timeout",
        )
        # Retry should update the same row
        run2 = record_digest_run(
            user_id=alice.id,
            frequency="daily",
            pkey="2026-01-02",
            period_start=since,
            period_end=until,
            count=2,
            status="sent",
        )
        assert run1.id == run2.id
        assert run2.status == "sent"
        assert run2.error_message is None

    def test_skipped_status(self, alice, db_session):
        since, until = period_window("daily", "2026-02-01")
        run = record_digest_run(
            user_id=alice.id,
            frequency="daily",
            pkey="2026-02-01",
            period_start=since,
            period_end=until,
            count=0,
            status="skipped",
        )
        assert run.status == "skipped"
        assert run.notification_count == 0

    def test_weekly_key(self, alice, db_session):
        since, until = period_window("weekly", "2026-W05")
        run = record_digest_run(
            user_id=alice.id,
            frequency="weekly",
            pkey="2026-W05",
            period_start=since,
            period_end=until,
            count=5,
            status="sent",
        )
        assert run.frequency == "weekly"
        assert run.period_key == "2026-W05"


# ── send_digest_for_user ──────────────────────────────────────────────────────


class TestSendDigestForUser:
    def test_skipped_when_no_notifications(self, alice, alice_pref, db_session):
        pkey = "2026-01-10"
        with patch("backend.email.mail_service.send_email") as mock_send:
            result = send_digest_for_user(alice.id, "daily", pkey)
        assert result == "skipped"
        mock_send.assert_not_called()
        # Run recorded as skipped
        run = db.session.scalar(
            db.select(DigestRun).where(
                DigestRun.user_id == alice.id,
                DigestRun.frequency == "daily",
                DigestRun.period_key == pkey,
            )
        )
        assert run is not None
        assert run.status == "skipped"

    def test_sent_when_notifications_exist(self, alice, alice_pref, db_session):
        since, until = period_window("daily", "2026-01-11")
        _notif(alice.id, created_at=since + timedelta(hours=1))

        with patch("backend.email.mail_service.send_email") as mock_send:
            result = send_digest_for_user(alice.id, "daily", "2026-01-11")

        assert result == "sent"
        mock_send.assert_called_once()
        # Verify email arguments
        call_kwargs = mock_send.call_args
        assert alice.email in (
            call_kwargs.args[0]
            if call_kwargs.args
            else call_kwargs.kwargs.get("to", "")
        )

        run = db.session.scalar(
            db.select(DigestRun).where(
                DigestRun.user_id == alice.id,
                DigestRun.period_key == "2026-01-11",
            )
        )
        assert run is not None
        assert run.status == "sent"
        assert run.notification_count == 1

    def test_idempotent_second_call(self, alice, alice_pref, db_session):
        """Calling send_digest_for_user twice for the same period must not send twice."""
        since, until = period_window("daily", "2026-01-12")
        _notif(alice.id, created_at=since + timedelta(hours=2))

        with patch("backend.email.mail_service.send_email") as mock_send:
            first = send_digest_for_user(alice.id, "daily", "2026-01-12")
            second = send_digest_for_user(alice.id, "daily", "2026-01-12")

        assert first == "sent"
        assert second == "already_sent"
        # Only one actual email
        assert mock_send.call_count == 1

    def test_updates_last_digest_sent_at(self, alice, alice_pref, db_session):
        since, until = period_window("daily", "2026-01-13")
        _notif(alice.id, created_at=since + timedelta(hours=3))

        with patch("backend.email.mail_service.send_email"):
            send_digest_for_user(alice.id, "daily", "2026-01-13")

        db.session.refresh(alice_pref)
        assert alice_pref.last_digest_sent_at is not None

    def test_failed_run_recorded_on_smtp_error(self, alice, alice_pref, db_session):
        since, until = period_window("daily", "2026-01-14")
        _notif(alice.id, created_at=since + timedelta(hours=4))

        with patch(
            "backend.email.mail_service.send_email",
            side_effect=RuntimeError("SMTP down"),
        ):
            with pytest.raises(RuntimeError, match="SMTP down"):
                send_digest_for_user(alice.id, "daily", "2026-01-14")

        run = db.session.scalar(
            db.select(DigestRun).where(
                DigestRun.user_id == alice.id,
                DigestRun.period_key == "2026-01-14",
            )
        )
        assert run is not None
        assert run.status == "failed"
        assert "SMTP down" in run.error_message

    def test_unknown_user_returns_skipped(self, db_session):
        result = send_digest_for_user(999_999, "daily", "2026-01-15")
        assert result == "skipped"

    def test_skipped_updates_last_sent_at(self, alice, alice_pref, db_session):
        """Even a skipped digest (no notifications) should update last_digest_sent_at."""
        with patch("backend.email.mail_service.send_email"):
            send_digest_for_user(alice.id, "daily", "2026-01-16")

        db.session.refresh(alice_pref)
        assert alice_pref.last_digest_sent_at is not None


# ── Celery fan-out tasks ──────────────────────────────────────────────────────


class TestDigestTasks:
    def test_send_daily_digests_enqueues_for_eligible_users(
        self, alice, alice_pref, db_session
    ):
        """send_daily_digests should trigger send_digest_for_user_task for each eligible user."""
        from backend.tasks.digests import send_daily_digests, send_digest_for_user_task

        with patch.object(send_digest_for_user_task, "delay", wraps=None) as mock_delay:
            mock_delay.return_value = MagicMock()
            send_daily_digests.apply()

        # alice has email_enabled=True and email_digest_frequency='daily'
        mock_delay.assert_called()
        call_args_list = mock_delay.call_args_list
        user_ids_dispatched = [c.args[0] for c in call_args_list]
        assert alice.id in user_ids_dispatched

    def test_send_weekly_digests_only_dispatches_weekly_users(
        self, alice, alice_pref, db_session, make_user_token
    ):
        """Weekly task must not dispatch for daily-only users."""
        from backend.tasks.digests import send_digest_for_user_task, send_weekly_digests

        # alice is 'daily' — should NOT be picked up by weekly task
        with patch.object(send_digest_for_user_task, "delay", wraps=None) as mock_delay:
            mock_delay.return_value = MagicMock()
            send_weekly_digests.apply()

        if mock_delay.called:
            user_ids_dispatched = [c.args[0] for c in mock_delay.call_args_list]
            assert alice.id not in user_ids_dispatched

    def test_send_weekly_digests_dispatches_weekly_user(
        self, alice, alice_pref, db_session
    ):
        """When a user is set to weekly, weekly fan-out should dispatch them."""
        from backend.tasks.digests import send_digest_for_user_task, send_weekly_digests

        alice_pref.email_digest_frequency = "weekly"
        db.session.commit()

        with patch.object(send_digest_for_user_task, "delay", wraps=None) as mock_delay:
            mock_delay.return_value = MagicMock()
            send_weekly_digests.apply()

        mock_delay.assert_called()
        user_ids_dispatched = [c.args[0] for c in mock_delay.call_args_list]
        assert alice.id in user_ids_dispatched

    def test_no_dispatch_when_email_disabled(self, alice, alice_pref, db_session):
        """Users with email_enabled=False must not receive digests."""
        from backend.tasks.digests import send_daily_digests, send_digest_for_user_task

        alice_pref.email_enabled = False
        db.session.commit()

        with patch.object(send_digest_for_user_task, "delay", wraps=None) as mock_delay:
            mock_delay.return_value = MagicMock()
            send_daily_digests.apply()

        if mock_delay.called:
            user_ids_dispatched = [c.args[0] for c in mock_delay.call_args_list]
            assert alice.id not in user_ids_dispatched
