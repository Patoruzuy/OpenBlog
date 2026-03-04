"""Celery task — execute an AI review request.

Task name: ``tasks.run_ai_review``

Behaviour
---------
1.  Load the :class:`~backend.models.ai_review.AIReviewRequest` row.
2.  Guard: skip if status is not ``queued`` (prevents double-execution on
    re-delivery after ``acks_late``).
3.  Mark status → ``running``, flush (non-commit) so the row is visible to
    concurrent readers while the task is in flight.
4.  Build the input text:
    - If ``revision_id``: compute unified diff (current body → proposed).
    - Else: use current ``post.markdown_body``.
    - Truncate to ``AI_MAX_INPUT_CHARS``.
5.  Call the configured AI provider.
6.  Write :class:`~backend.models.ai_review.AIReviewResult`.
7.  Mark status → ``completed``, ``completed_at`` = now.
8.  Commit.

On exception: mark status → ``failed``, store ``error_message``, commit,
then raise :meth:`self.retry` with exponential backoff:
    attempt 0 → wait 10 s
    attempt 1 → wait 40 s
    attempt 2 → wait 160 s  (~2.5 min)
A terminal failure (retries exhausted) keeps the row in ``failed`` state
so the user sees a clear error in the UI instead of a stale ``running`` row.

``acks_late=True`` ensures the broker acks the task only after it completes
successfully, which together with ``reject_on_worker_lost=True`` means a
worker crash requeues the job automatically.
"""

from __future__ import annotations

from celery import shared_task


@shared_task(
    bind=True,
    max_retries=3,
    name="tasks.run_ai_review",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_ai_review(self, request_id: int) -> dict:  # type: ignore[override]
    """Execute an AI review for *request_id* and persist the result.

    Parameters
    ----------
    request_id:
        Primary key of the :class:`~backend.models.ai_review.AIReviewRequest`
        row to process.

    Returns
    -------
    dict
        Summary: ``{"request_id": int, "status": str}``.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from flask import current_app  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.ai_review import (  # noqa: PLC0415
        AIReviewRequest,
        AIReviewResult,
        AIReviewStatus,
    )
    from backend.models.post import Post  # noqa: PLC0415
    from backend.models.revision import Revision  # noqa: PLC0415

    req: AIReviewRequest | None = db.session.get(AIReviewRequest, request_id)
    if req is None:
        # Row deleted between enqueue and execution — nothing to do.
        return {"request_id": request_id, "status": "not_found"}

    # Guard: if already running/completed/failed (e.g. duplicate delivery),
    # skip to avoid overwriting a finished result.
    if req.status not in (AIReviewStatus.queued.value,):
        return {"request_id": request_id, "status": req.status}

    try:
        # ── Step 3: mark running ──────────────────────────────────────────
        req.status = AIReviewStatus.running.value
        req.started_at = datetime.now(UTC)
        db.session.flush()

        # ── Step 4: build input text ──────────────────────────────────────
        post: Post | None = db.session.get(Post, req.post_id)
        if post is None:
            raise RuntimeError(f"Post {req.post_id} not found for review {request_id}.")

        revision: Revision | None = None
        if req.revision_id:
            revision = db.session.get(Revision, req.revision_id)

        if revision is not None:
            from backend.utils.diff import compute_diff  # noqa: PLC0415

            raw_text = compute_diff(
                post.markdown_body or "",
                revision.proposed_markdown or "",
            )
            if not raw_text.strip():
                raw_text = revision.proposed_markdown or ""
        else:
            raw_text = post.markdown_body or ""

        max_chars: int = current_app.config.get("AI_MAX_INPUT_CHARS", 32768)
        input_text = raw_text[:max_chars]

        # ── Step 5: call provider ─────────────────────────────────────────
        from backend.ai.providers import get_provider  # noqa: PLC0415

        provider = get_provider(current_app.config)
        context = {
            "post_title": post.title,
            "workspace_id": req.workspace_id,
            "review_type": req.review_type,
            "has_revision": revision is not None,
        }
        result_data = provider.run_review(input_text, req.review_type, context)

        # ── Cancel guard ─────────────────────────────────────────────────
        # Re-fetch the request row to pick up any status change made while
        # the provider was running (e.g. an admin canceled it via Ops UI).
        # expire() forces SQLAlchemy to reload the row on the next access.
        db.session.expire(req)
        req = db.session.get(AIReviewRequest, request_id)
        if req is None or req.status == AIReviewStatus.canceled.value:
            db.session.rollback()
            return {"request_id": request_id, "status": "canceled"}

        # ── Step 6: persist result ────────────────────────────────────────
        result = AIReviewResult(
            request_id=req.id,
            provider=provider.name,
            model_name=current_app.config.get("AI_MODEL_NAME") or "mock-model-v1",
            prompt_version="ai-review-v1",
            summary_md=result_data.get("summary_md", ""),
            findings_json=result_data.get("findings_json", []),
            metrics_json=result_data.get("metrics_json", {}),
            suggested_edits_json=result_data.get("suggested_edits_json", {}),
            created_at=datetime.now(UTC),
        )
        db.session.add(result)

        # ── Step 7: mark completed ────────────────────────────────────────
        req.status = AIReviewStatus.completed.value
        req.completed_at = datetime.now(UTC)
        req.error_message = None

        # ── Step 8: commit ────────────────────────────────────────────────
        db.session.commit()

        # Fanout in-app notification to subscribers / requester.
        from backend.services.notification_service import emit as _emit  # noqa: PLC0415

        _emit(
            "ai_review.completed",
            req.requested_by_user_id,
            "post",
            req.post_id,
            {
                "request_id": req.id,
                "review_type": req.review_type,
                "workspace_id": req.workspace_id,
                "requester_id": req.requested_by_user_id,
                "post_title": post.title if post else "",
            },
        )

        return {"request_id": req.id, "status": AIReviewStatus.completed.value}

    except Exception as exc:  # noqa: BLE001
        # ── Failure path ──────────────────────────────────────────────────
        db.session.rollback()

        # Reload after rollback — the row state was lost.
        req = db.session.get(AIReviewRequest, request_id)
        if req is not None:
            req.status = AIReviewStatus.failed.value
            req.error_message = str(exc)[:2000]
            try:
                db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()

        # Exponential backoff: 10 s → 40 s → 160 s.
        countdown = 10 * (4**self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
