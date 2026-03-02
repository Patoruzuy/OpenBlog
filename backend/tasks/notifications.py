"""Celery tasks for thread notification delivery and event fanout."""

from __future__ import annotations

from celery import shared_task


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="tasks.notify_thread_comment_created",
)
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


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    name="tasks.notifications.fanout",
)
def fanout(  # type: ignore[override]
    self,
    *,
    event_type: str,
    actor_user_id: int | None,
    target_type: str,
    target_id: int,
    payload: dict,
) -> None:
    """Fan out an event to all eligible subscribers.

    Steps
    -----
    1. Resolve recipients (subscriptions + direct participants).
    2. Filter to those who still have access to the target content.
    3. Skip self-notifications (actor == recipient).
    4. Create deduplicated Notification rows.

    Retried up to 3 times on transient errors (unique fingerprint prevents
    duplicate rows on retry).
    """
    try:
        from backend.extensions import db  # noqa: PLC0415
        from backend.services.notification_service import (  # noqa: PLC0415
            create_notification_for_user,
            filter_recipients_by_access,
            get_recipients,
        )

        recipients = get_recipients(event_type, target_type, target_id, payload)
        accessible = filter_recipients_by_access(recipients, target_type, target_id)

        created = 0
        for user_id in accessible:
            if user_id == actor_user_id:
                continue  # never self-notify

            notif = create_notification_for_user(
                user_id=user_id,
                event_type=event_type,
                actor_user_id=actor_user_id,
                target_type=target_type,
                target_id=target_id,
                payload=payload,
            )
            if notif is not None:
                created += 1

        if created:
            db.session.commit()

    except Exception as exc:
        raise self.retry(exc=exc)
