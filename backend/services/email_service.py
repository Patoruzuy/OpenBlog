"""Email service — template rendering + delivery logging.

All email sending goes through ``EmailService.send_template`` which:
1. Creates a queued ``EmailDeliveryLog`` row synchronously.
2. Enqueues a Celery task that actually delivers the message.
3. The task updates the log row to "sent" or "failed".

This means every outbound email is traceable even if Celery is down.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from backend.extensions import db
from backend.models.email_delivery_log import EmailDeliveryLog


class EmailService:
    """Facade for rendering and sending transactional emails."""

    @staticmethod
    def queue(
        to_email: str,
        subject: str,
        template_key: str,
        context: dict,
        *,
        locale: str = "en",
        metadata: dict | None = None,
    ) -> int:
        """Create a delivery log row (status="queued") and enqueue a Celery task.

        Returns the log row ``id`` which the Celery task uses to update status.
        """
        meta_json = json.dumps(metadata) if metadata else None
        log = EmailDeliveryLog(
            to_email=to_email.strip().lower(),
            template_key=template_key,
            subject=subject,
            status="queued",
            metadata_json=meta_json,
            created_at=datetime.now(UTC),
        )
        db.session.add(log)
        db.session.commit()

        # Import task lazily to avoid circular imports and missing app context.
        from backend.tasks.email import deliver_email  # noqa: PLC0415

        deliver_email.delay(log.id, to_email, subject, template_key, context, locale)
        return log.id

    @staticmethod
    def mark_sent(log_id: int, provider_message_id: str | None = None) -> None:
        log = db.session.get(EmailDeliveryLog, log_id)
        if log:
            log.status = "sent"
            log.sent_at = datetime.now(UTC)
            log.provider_message_id = provider_message_id
            db.session.commit()

    @staticmethod
    def mark_failed(log_id: int, error: str) -> None:
        log = db.session.get(EmailDeliveryLog, log_id)
        if log:
            log.status = "failed"
            log.error_message = error[:1000]
            db.session.commit()

    @staticmethod
    def recent_failures(limit: int = 50) -> list[EmailDeliveryLog]:
        return list(
            db.session.scalars(
                select(EmailDeliveryLog)
                .where(EmailDeliveryLog.status == "failed")
                .order_by(EmailDeliveryLog.created_at.desc())
                .limit(limit)
            ).all()
        )
