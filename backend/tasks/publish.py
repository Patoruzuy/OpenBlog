"""Celery task: publish scheduled posts.

This module is imported by the Celery worker at startup so the task is
registered and discoverable by the beat scheduler.

Beat configuration (add to config or celeryconfig):
----------------------------------------------------
CELERYBEAT_SCHEDULE = {
    "publish-scheduled-posts": {
        "task": "tasks.publish_scheduled_posts",
        "schedule": 60.0,  # every 60 seconds
    },
}
"""

from __future__ import annotations

from celery import shared_task


@shared_task(name="tasks.publish_scheduled_posts")
def publish_scheduled_posts() -> dict:
    """Transition all due ``scheduled`` posts to ``published``.

    Runs inside a Flask app context (guaranteed by the FlaskTask base class
    configured in ``extensions._make_celery``).

    Returns a summary dict so the beat log shows what was processed.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from backend.extensions import db
    from backend.models.post import Post, PostStatus

    now = datetime.now(UTC)
    due: list[Post] = list(
        db.session.scalars(
            select(Post).where(
                Post.status == PostStatus.scheduled,
                Post.publish_at <= now,
            )
        ).all()
    )

    for post in due:
        post.status = PostStatus.published
        post.published_at = post.publish_at

    if due:
        db.session.commit()
        from backend.utils import metrics  # noqa: PLC0415

        for _ in due:
            metrics.posts_published.inc()

    return {"published": [p.slug for p in due], "count": len(due)}
