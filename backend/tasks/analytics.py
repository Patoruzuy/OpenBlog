"""Celery task: flush the Redis analytics event queue to the database.

This task is designed to be run on a short schedule (e.g. every 30 seconds)
by Celery Beat so that analytics events buffered by ``AnalyticsService.queue_event()``
are persisted promptly without blocking web requests.

Beat configuration (add to CELERYBEAT_SCHEDULE):
-------------------------------------------------
"flush-analytics-queue": {
    "task": "tasks.flush_analytics_queue",
    "schedule": 30.0,  # every 30 seconds
},
"""

from __future__ import annotations

from celery import shared_task


@shared_task(name="tasks.flush_analytics_queue")
def flush_analytics_queue() -> dict:
    """Drain the Redis analytics queue and bulk-insert events to the DB.

    Runs inside a Flask app context (guaranteed by the FlaskTask base class
    configured in ``extensions._make_celery``).

    Returns a summary dict so the beat log shows what was processed.
    """
    from backend.services.analytics_service import AnalyticsService

    count = AnalyticsService.flush_queued_events()
    return {"flushed": count}
