"""Celery tasks for thread notification delivery."""

from __future__ import annotations

from celery import shared_task


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="tasks.notify_thread_comment_created")
def notify_thread_comment_created(  # type: ignore[override]
    self,
    payload: dict,
) -> None:
    """Deliver in-app + email notifications after a comment is created.

    *payload* keys: ``post_id``, ``comment_id``, ``author_id``,
    ``parent_id`` (may be ``None``), ``body``.

    Retried up to 3 times with a 60-second back-off on transient errors.
    """
    try:
        from backend.services.notification_delivery_service import (  # noqa: PLC0415
            NotificationDeliveryService,
        )

        NotificationDeliveryService.process_comment_created(
            post_id=payload["post_id"],
            comment_id=payload["comment_id"],
            author_id=payload["author_id"],
            parent_id=payload.get("parent_id"),
            body=payload["body"],
        )
    except Exception as exc:
        raise self.retry(exc=exc)
