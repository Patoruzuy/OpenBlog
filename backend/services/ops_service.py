"""Ops service — operational visibility into async systems.

Provides the data layer for the Admin Ops Dashboard:

  get_health_snapshot()             — DB / Redis / Celery status
  get_ai_review_stats(hours=24)     — per-status counts
  list_ai_review_requests(filters)  — latest N requests (bounded)
  retry_ai_review_request(id)       — re-queue a failed/canceled request
  cancel_ai_review_request(id)      — mark queued/running as canceled
  list_digest_runs(filters)         — latest N digest_runs
  retry_digest_run(id)              — re-enqueue a failed digest
  get_notification_stats()          — aggregate counts (24 h / 7 d)

Guarding contract
-----------------
All write operations (retry / cancel) raise :class:`OpsError` on invalid
state transitions so the route layer can surface a clean 400 message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text

from backend.extensions import db
from backend.models.ai_review import AIReviewRequest, AIReviewStatus
from backend.models.digest_run import DigestRun
from backend.models.notification import Notification

# ── Domain error ──────────────────────────────────────────────────────────────

_ERROR_MSG_MAX = 400  # characters shown in UI per error_message field


class OpsError(Exception):
    """Raised for invalid state transitions or missing rows."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── Health ────────────────────────────────────────────────────────────────────


def get_health_snapshot() -> dict[str, Any]:
    """Return lightweight health indicators for all three async backends.

    All checks are best-effort; a failure is reported as an informative
    ``ok=False`` dict rather than raising an exception.
    """
    return {
        "db": _check_db(),
        "redis": _check_redis(),
        "celery": _check_celery(),
    }


def _check_db() -> dict:
    try:
        db.session.execute(text("SELECT 1"))
        return {"ok": True, "label": "Connected"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "label": "Error", "error": str(exc)[:120]}


def _check_redis() -> dict:
    try:
        from flask import current_app  # noqa: PLC0415

        redis = current_app.extensions.get("redis")
        if redis is None:
            return {"ok": False, "label": "Not configured"}
        redis.ping()
        return {"ok": True, "label": "PONG"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "label": "Error", "error": str(exc)[:120]}


def _check_celery() -> dict:
    try:
        from backend.extensions import celery  # noqa: PLC0415

        broker: str = celery.conf.broker_url or "(not configured)"
        inspect = celery.control.inspect(timeout=1.5)
        stats = inspect.stats()
        if stats:
            return {
                "ok": True,
                "label": f"{len(stats)} worker(s)",
                "broker": broker,
            }
        return {"ok": False, "label": "No workers responding", "broker": broker}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "label": str(exc)[:120]}


# ── AI Reviews ────────────────────────────────────────────────────────────────


def get_ai_review_stats(hours: int = 24) -> dict[str, int]:
    """Return per-status counts of AI review requests in the last *hours* hours."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows = db.session.execute(
        select(AIReviewRequest.status, func.count(AIReviewRequest.id))
        .where(AIReviewRequest.created_at >= cutoff)
        .group_by(AIReviewRequest.status)
    ).all()
    counts = {s.value: 0 for s in AIReviewStatus}
    for status_val, cnt in rows:
        counts[status_val] = cnt
    return counts


def list_ai_review_requests(
    *,
    status: str | None = None,
    workspace_id: int | None = None,
    limit: int = 100,
) -> list[AIReviewRequest]:
    """Return latest *limit* AI review requests, newest first.

    Parameters are optional filters.  ``limit`` is capped at 200 to keep
    the query bounded.
    """
    limit = min(limit, 200)
    q = select(AIReviewRequest).order_by(AIReviewRequest.created_at.desc())
    if status:
        q = q.where(AIReviewRequest.status == status)
    if workspace_id is not None:
        q = q.where(AIReviewRequest.workspace_id == workspace_id)
    q = q.limit(limit)
    return list(db.session.scalars(q).all())


def retry_ai_review_request(request_id: int) -> AIReviewRequest:
    """Reset *request_id* to ``queued`` and re-enqueue the Celery task.

    Only ``failed`` and ``canceled`` requests may be retried.  Any other
    state raises :class:`OpsError` (400 equivalent) so the caller can return
    a clear HTTP 400.

    Idempotency: if the row is already ``queued`` or ``running`` this also
    raises OpsError rather than creating a duplicate task.
    """
    req: AIReviewRequest | None = db.session.get(AIReviewRequest, request_id)
    if req is None:
        raise OpsError(f"AI review request #{request_id} not found.", status_code=404)

    retriable = {AIReviewStatus.failed.value, AIReviewStatus.canceled.value}
    if req.status not in retriable:
        raise OpsError(
            f"Cannot retry request in {req.status!r} state. "
            "Only failed or canceled requests may be retried.",
            status_code=400,
        )

    # Reset the row to a clean queued state.
    req.status = AIReviewStatus.queued.value
    req.started_at = None
    req.completed_at = None
    req.error_message = None
    db.session.commit()

    from backend.tasks.ai_reviews import run_ai_review  # noqa: PLC0415

    run_ai_review.delay(req.id)
    return req


def cancel_ai_review_request(request_id: int) -> AIReviewRequest:
    """Mark *request_id* as ``canceled``.

    Only ``queued`` and ``running`` requests may be canceled.
    """
    req: AIReviewRequest | None = db.session.get(AIReviewRequest, request_id)
    if req is None:
        raise OpsError(f"AI review request #{request_id} not found.", status_code=404)

    cancelable = {AIReviewStatus.queued.value, AIReviewStatus.running.value}
    if req.status not in cancelable:
        raise OpsError(
            f"Cannot cancel request in {req.status!r} state. "
            "Only queued or running requests may be canceled.",
            status_code=400,
        )

    req.status = AIReviewStatus.canceled.value
    req.completed_at = datetime.now(UTC)
    db.session.commit()
    return req


# ── Digests ───────────────────────────────────────────────────────────────────


def list_digest_runs(
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[DigestRun]:
    """Return latest *limit* digest_runs, newest first."""
    limit = min(limit, 200)
    q = (
        select(DigestRun)
        .order_by(DigestRun.period_start.desc())
        .limit(limit)
    )
    if status:
        q = q.where(DigestRun.status == status)
    return list(db.session.scalars(q).all())


def retry_digest_run(digest_run_id: int) -> DigestRun:
    """Re-enqueue a failed digest run for the original user / period.

    Deletes the existing :class:`DigestRun` row so the idempotency guard
    in :func:`~backend.services.digest_service.send_digest_for_user` does
    not short-circuit the retry, then enqueues
    ``tasks.digests.send_digest_for_user``.

    Only ``failed`` digest runs may be retried.
    """
    run: DigestRun | None = db.session.get(DigestRun, digest_run_id)
    if run is None:
        raise OpsError(f"Digest run #{digest_run_id} not found.", status_code=404)
    if run.status != "failed":
        raise OpsError(
            f"Cannot retry digest run in {run.status!r} state. "
            "Only failed runs may be retried.",
            status_code=400,
        )

    # Capture fields before deletion.
    user_id = run.user_id
    frequency = run.frequency
    period_key = run.period_key

    db.session.delete(run)
    db.session.commit()

    from backend.tasks.digests import send_digest_for_user_task  # noqa: PLC0415

    send_digest_for_user_task.delay(user_id, frequency, period_key)

    # Return a transient object describing what was retried (row no longer in DB).
    synthetic = DigestRun(
        user_id=user_id,
        frequency=frequency,
        period_key=period_key,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
        status="queued",
    )
    return synthetic


# ── Notifications ─────────────────────────────────────────────────────────────


def get_notification_stats() -> dict[str, Any]:
    """Return aggregate notification counts and top event types.

    Looks back 24 h and 7 d.  All queries use the indexed ``created_at``
    column so they're fast even on large tables.
    """
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    count_24h: int = (
        db.session.scalar(
            select(func.count(Notification.id)).where(
                Notification.created_at >= cutoff_24h
            )
        )
        or 0
    )
    count_7d: int = (
        db.session.scalar(
            select(func.count(Notification.id)).where(
                Notification.created_at >= cutoff_7d
            )
        )
        or 0
    )

    # Top 10 event types in the last 7 days (event_type may be NULL for legacy rows).
    top_types_rows = db.session.execute(
        select(
            Notification.event_type,
            func.count(Notification.id).label("cnt"),
        )
        .where(
            Notification.created_at >= cutoff_7d,
            Notification.event_type.isnot(None),
        )
        .group_by(Notification.event_type)
        .order_by(func.count(Notification.id).desc())
        .limit(10)
    ).all()

    top_types = [
        {"event_type": row.event_type, "count": row.cnt} for row in top_types_rows
    ]

    return {
        "count_24h": count_24h,
        "count_7d": count_7d,
        "top_event_types": top_types,
    }


# ── Safe display helper ────────────────────────────────────────────────────────


def _truncate_error(msg: str | None) -> str | None:
    """Truncate an error message to a safe display length."""
    if msg is None:
        return None
    return msg[:_ERROR_MSG_MAX]
