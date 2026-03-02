"""Celery tasks for periodic email digest delivery.

Schedule (Celery-beat)
----------------------
``send_daily_digests``   fires once a day at 09:00 UTC.
``send_weekly_digests``  fires once a week on Monday at 09:00 UTC.

Both fan-out tasks iterate over eligible users and enqueue
``send_digest_for_user_task`` for each one, keeping the fan-out task
itself short-lived.

``send_digest_for_user_task`` does the heavy lifting:
  1. Idempotency check (digest_runs unique key).
  2. Build digest from notification rows in the period window.
  3. Render and send email.
  4. Record digest_runs row.

Retry behaviour
---------------
``send_digest_for_user_task`` retries up to 3 times with 5-minute
back-off on transient errors (SMTP blip, DB deadlock, etc.).  The
idempotency guard in :func:`~backend.services.digest_service.send_digest_for_user`
means retries are always safe.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery import shared_task

log = logging.getLogger(__name__)


@shared_task(
    name="tasks.digests.send_daily_digests",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_daily_digests(self) -> None:  # type: ignore[override]
    """Fan-out task: enqueue ``send_digest_for_user_task`` for all daily-eligible users."""
    try:
        _enqueue_digests("daily")
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name="tasks.digests.send_weekly_digests",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_weekly_digests(self) -> None:  # type: ignore[override]
    """Fan-out task: enqueue ``send_digest_for_user_task`` for all weekly-eligible users."""
    try:
        _enqueue_digests("weekly")
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name="tasks.digests.send_digest_for_user",
    bind=True,
    max_retries=3,
    default_retry_delay=300,  # 5-minute back-off between retries
    acks_late=True,
)
def send_digest_for_user_task(  # type: ignore[override]
    self,
    user_id: int,
    frequency: str,
    pkey: str,
) -> str:
    """Send a single digest email for *user_id*.

    Parameters
    ----------
    user_id:    Recipient user PK.
    frequency:  ``'daily'`` or ``'weekly'``.
    pkey:       Period key, e.g. ``'2026-03-02'`` or ``'2026-W10'``.

    Returns the status string produced by
    :func:`~backend.services.digest_service.send_digest_for_user`.
    """
    try:
        from backend.services.digest_service import (
            send_digest_for_user,  # noqa: PLC0415
        )

        return send_digest_for_user(user_id, frequency, pkey)
    except Exception as exc:
        log.exception(
            "Digest delivery failed for user=%s freq=%s period=%s: %s",
            user_id,
            frequency,
            pkey,
            exc,
        )
        raise self.retry(exc=exc)


# ── Fan-out helper ────────────────────────────────────────────────────────────


def _enqueue_digests(frequency: str) -> None:
    """Query eligible users and enqueue one task per user.

    Eligible: ``notification_preferences.email_digest_frequency == frequency``
    AND       ``email_enabled = true``
    """
    from sqlalchemy import select  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.notification_preference import (
        NotificationPreference,  # noqa: PLC0415
    )
    from backend.services.digest_service import period_key  # noqa: PLC0415

    now = datetime.now(UTC)
    pkey = period_key(frequency, now)

    prefs = db.session.scalars(
        select(NotificationPreference).where(
            NotificationPreference.email_digest_frequency == frequency,
            NotificationPreference.email_enabled.is_(True),
        )
    ).all()

    queued = 0
    for pref in prefs:
        send_digest_for_user_task.delay(pref.user_id, frequency, pkey)
        queued += 1

    log.info(
        "_enqueue_digests: freq=%s period=%s queued=%d",
        frequency,
        pkey,
        queued,
    )
