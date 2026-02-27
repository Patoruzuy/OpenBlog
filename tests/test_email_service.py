"""Tests for EmailService and EmailDeliveryLog lifecycle.

Scenarios covered
-----------------
- EmailService.queue() creates EmailDeliveryLog with status="queued"
- mark_sent() updates status to "sent" and records sent_at
- mark_failed() updates status to "failed" with error message
- recent_failures() returns only failed logs in descending order
- Email HTML templates are renderable (no Jinja errors)
- Email plain-text templates are renderable
- Celery task (deliver_email) renders templates and calls mail.send
- deliver_email marks log "sent" on success
- deliver_email marks log "failed" after max retries exhausted
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.extensions import db as _db
from backend.models.email_delivery_log import EmailDeliveryLog
from backend.services.email_service import EmailService


# ── EmailService unit tests ───────────────────────────────────────────────────


class TestEmailServiceQueue:
    def test_queue_creates_log_row(self, db_session, app):  # noqa: ARG002
        with patch("backend.tasks.email.deliver_email.delay"):
            log_id = EmailService.queue(
                "user@example.com",
                "Test Subject",
                "password_reset",
                {"reset_url": "https://example.com/reset"},
            )

        assert log_id is not None
        log = _db.session.get(EmailDeliveryLog, log_id)
        assert log is not None
        assert log.status == "queued"
        assert log.to_email == "user@example.com"
        assert log.template_key == "password_reset"
        assert log.subject == "Test Subject"

    def test_queue_normalises_email_to_lowercase(self, db_session, app):  # noqa: ARG002
        with patch("backend.tasks.email.deliver_email.delay"):
            log_id = EmailService.queue(
                "UPPER@EXAMPLE.COM",
                "Subject",
                "verify_email",
                {},
            )

        log = _db.session.get(EmailDeliveryLog, log_id)
        assert log.to_email == "upper@example.com"

    def test_queue_stores_metadata(self, db_session, app):  # noqa: ARG002
        with patch("backend.tasks.email.deliver_email.delay"):
            log_id = EmailService.queue(
                "meta@example.com",
                "Subject",
                "verify_email",
                {},
                metadata={"source": "test", "ref": 42},
            )

        log = _db.session.get(EmailDeliveryLog, log_id)
        import json
        assert log.metadata_json is not None
        parsed = json.loads(log.metadata_json)
        assert parsed["source"] == "test"
        assert parsed["ref"] == 42

    def test_queue_fires_celery_task(self, db_session, app):  # noqa: ARG002
        with patch("backend.tasks.email.deliver_email.delay") as mock_delay:
            log_id = EmailService.queue(
                "task@example.com",
                "Subject",
                "password_reset",
                {"reset_url": "https://example.com/reset"},
            )
            mock_delay.assert_called_once()
            args = mock_delay.call_args[0]
            # First arg is log_id, second is to_email
            assert args[0] == log_id
            assert args[1] == "task@example.com"


class TestEmailServiceMarkSent:
    def test_mark_sent_updates_status(self, db_session):  # noqa: ARG002
        log = EmailDeliveryLog(
            to_email="a@example.com",
            template_key="verify_email",
            subject="Test",
            status="queued",
        )
        _db.session.add(log)
        _db.session.commit()

        EmailService.mark_sent(log.id, provider_message_id="smtp-abc123")

        updated = _db.session.get(EmailDeliveryLog, log.id)
        assert updated.status == "sent"
        assert updated.sent_at is not None
        assert updated.provider_message_id == "smtp-abc123"

    def test_mark_sent_unknown_id_is_noop(self, db_session):  # noqa: ARG002
        # Should not raise for unknown log IDs
        EmailService.mark_sent(999999)


class TestEmailServiceMarkFailed:
    def test_mark_failed_updates_status(self, db_session):  # noqa: ARG002
        log = EmailDeliveryLog(
            to_email="b@example.com",
            template_key="password_reset",
            subject="Reset",
            status="queued",
        )
        _db.session.add(log)
        _db.session.commit()

        EmailService.mark_failed(log.id, "SMTP connection refused")

        updated = _db.session.get(EmailDeliveryLog, log.id)
        assert updated.status == "failed"
        assert "SMTP" in updated.error_message

    def test_mark_failed_truncates_long_error(self, db_session):  # noqa: ARG002
        log = EmailDeliveryLog(
            to_email="c@example.com",
            template_key="verify_email",
            subject="Verify",
            status="queued",
        )
        _db.session.add(log)
        _db.session.commit()

        long_error = "E" * 5000
        EmailService.mark_failed(log.id, long_error)

        updated = _db.session.get(EmailDeliveryLog, log.id)
        assert len(updated.error_message) <= 1000

    def test_mark_failed_unknown_id_is_noop(self, db_session):  # noqa: ARG002
        EmailService.mark_failed(999999, "error")


class TestEmailServiceRecentFailures:
    def test_recent_failures_returns_failed_only(self, db_session):  # noqa: ARG002
        logs = [
            EmailDeliveryLog(to_email=f"u{i}@e.com", template_key="verify_email",
                             subject=f"S{i}", status=s)
            for i, s in enumerate(["failed", "sent", "failed", "queued"])
        ]
        for log in logs:
            _db.session.add(log)
        _db.session.commit()

        failures = EmailService.recent_failures(limit=50)
        statuses = {f.status for f in failures}
        assert statuses == {"failed"}
        assert len(failures) == 2

    def test_recent_failures_respects_limit(self, db_session):  # noqa: ARG002
        for i in range(10):
            log = EmailDeliveryLog(to_email=f"f{i}@e.com", template_key="verify_email",
                                   subject=f"S{i}", status="failed")
            _db.session.add(log)
        _db.session.commit()

        failures = EmailService.recent_failures(limit=3)
        assert len(failures) == 3

    def test_recent_failures_empty_when_no_failures(self, db_session):  # noqa: ARG002
        failures = EmailService.recent_failures()
        assert failures == []


# ── Template rendering tests ──────────────────────────────────────────────────


class TestEmailTemplateRendering:
    """Verify that all email templates render without Jinja errors."""

    _CONTEXTS = {
        "password_reset": {"reset_url": "https://example.com/reset/TOKEN"},
        "verify_email": {"verification_url": "https://example.com/verify/TOKEN",
                         "username": "testuser"},
        "newsletter_confirm": {
            "confirm_url": "https://example.com/newsletter/confirm?token=TOKEN",
            "unsubscribe_url": "https://example.com/newsletter/unsubscribe?token=TOKEN",
            "locale": "en",
        },
    }

    def test_password_reset_html(self, app):
        with app.test_request_context():
            from flask import render_template

            html = render_template("email/password_reset.html",
                                   **self._CONTEXTS["password_reset"])
        assert "reset" in html.lower() or "password" in html.lower()

    def test_password_reset_txt(self, app):
        with app.test_request_context():
            from flask import render_template

            txt = render_template("email/password_reset.txt",
                                  **self._CONTEXTS["password_reset"])
        assert "reset" in txt.lower() or "password" in txt.lower()

    def test_verify_email_html(self, app):
        with app.test_request_context():
            from flask import render_template

            html = render_template("email/verify_email.html",
                                   **self._CONTEXTS["verify_email"])
        assert "verif" in html.lower() or "confirm" in html.lower()

    def test_verify_email_txt(self, app):
        with app.test_request_context():
            from flask import render_template

            txt = render_template("email/verify_email.txt",
                                  **self._CONTEXTS["verify_email"])
        assert len(txt.strip()) > 0

    def test_newsletter_confirm_html(self, app):
        with app.test_request_context():
            from flask import render_template

            html = render_template("email/newsletter_confirm.html",
                                   **self._CONTEXTS["newsletter_confirm"])
        assert "confirm" in html.lower() or "subscribe" in html.lower()

    def test_newsletter_confirm_txt(self, app):
        with app.test_request_context():
            from flask import render_template

            txt = render_template("email/newsletter_confirm.txt",
                                  **self._CONTEXTS["newsletter_confirm"])
        assert len(txt.strip()) > 0


# ── Celery task unit tests ────────────────────────────────────────────────────


class TestDeliverEmailTask:
    """Test the deliver_email Celery task by calling .run() with a pushed request context."""

    def test_marks_sent_on_successful_delivery(self, db_session, app):  # noqa: ARG002
        log = EmailDeliveryLog(
            to_email="celery@example.com",
            template_key="password_reset",
            subject="Reset your password",
            status="queued",
        )
        _db.session.add(log)
        _db.session.commit()
        log_id = log.id

        from backend.tasks.email import deliver_email  # noqa: PLC0415

        # push_request gives deliver_email a proper request context with retries=0
        deliver_email.push_request(retries=0)
        try:
            with app.test_request_context("/"):
                with patch("flask_mail.Mail.send", return_value=None):
                    deliver_email.run(
                        log_id,
                        "celery@example.com",
                        "Reset your password",
                        "password_reset",
                        {"reset_url": "https://example.com/reset/TOKEN"},
                        "en",
                    )
        finally:
            deliver_email.pop_request()

        updated = _db.session.get(EmailDeliveryLog, log_id)
        assert updated.status == "sent"

    def test_marks_failed_when_max_retries_reached(self, db_session, app):  # noqa: ARG002
        log = EmailDeliveryLog(
            to_email="fail@example.com",
            template_key="verify_email",
            subject="Verify",
            status="queued",
        )
        _db.session.add(log)
        _db.session.commit()
        log_id = log.id

        from backend.tasks.email import deliver_email  # noqa: PLC0415

        # push request at retries == max_retries so mark_failed is triggered
        deliver_email.push_request(retries=deliver_email.max_retries)
        try:
            with app.test_request_context("/"):
                with patch("flask_mail.Mail.send", side_effect=Exception("SMTP down")):
                    with pytest.raises(Exception):  # Retry or direct exception
                        deliver_email.run(
                            log_id,
                            "fail@example.com",
                            "Verify",
                            "verify_email",
                            {"verification_url": "http://x.com/v", "username": "u"},
                            "en",
                        )
        finally:
            deliver_email.pop_request()

        updated = _db.session.get(EmailDeliveryLog, log_id)
        assert updated.status == "failed"
        assert updated.error_message is not None
