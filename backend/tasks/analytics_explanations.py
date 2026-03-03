"""Celery task — generate an AI analytics explanation.

Task name: ``tasks.generate_analytics_explanation``

Behaviour
---------
1.  Load the :class:`~backend.models.analytics_explanation.AnalyticsExplanation` row.
2.  Guard: skip if status is not ``queued`` (prevents double-execution on
    re-delivery after ``acks_late``).
3.  Mark status → ``running``, flush (non-commit).
4.  Re-build the input dict via ``build_input()`` (never trusts stale state,
    per AGENTS.md §2.4).
5.  Truncate overall JSON to ``AI_MAX_INPUT_CHARS``.
6.  Call ``provider.run_explanation(input_dict, kind)`` → plain Markdown string.
7.  Cancel guard: re-fetch the row to detect any intervening status change.
8.  Truncate output to 3 000 chars.
9.  Mark status → ``completed``, store ``explanation_md``.
10. Commit.

On exception: mark status → ``failed``, store ``error_message`` (max 400 chars,
per AGENTS.md §2.4), commit, then raise ``self.retry`` with exponential backoff:
    attempt 0 → wait 10 s
    attempt 1 → wait 40 s
    attempt 2 → wait 160 s  (~2.5 min)

``acks_late=True`` + ``reject_on_worker_lost=True`` ensure worker crashes
requeue the job automatically.
"""

from __future__ import annotations

from celery import shared_task

_MAX_OUTPUT_CHARS = 3_000
_MAX_ERROR_CHARS = 400  # AGENTS.md §2.4


@shared_task(
    bind=True,
    max_retries=3,
    name="tasks.generate_analytics_explanation",
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_analytics_explanation(
    self,
    explanation_id: int,
) -> dict:  # type: ignore[override]
    """Generate an AI analytics explanation and persist the result.

    Parameters
    ----------
    explanation_id:
        Primary key of the
        :class:`~backend.models.analytics_explanation.AnalyticsExplanation` row.

    Returns
    -------
    dict
        Summary: ``{"explanation_id": int, "status": str}``.
    """
    import json  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from flask import current_app  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.analytics_explanation import (  # noqa: PLC0415
        AnalyticsExplanation,
        AnalyticsExplanationStatus,
    )
    from backend.models.post import Post  # noqa: PLC0415
    from backend.models.workspace import Workspace  # noqa: PLC0415

    row: AnalyticsExplanation | None = db.session.get(
        AnalyticsExplanation, explanation_id
    )
    if row is None:
        # Row deleted between enqueue and execution — nothing to do.
        return {"explanation_id": explanation_id, "status": "not_found"}

    # Guard: skip if already running/completed/failed (duplicate delivery).
    if row.status not in (AnalyticsExplanationStatus.queued.value,):
        return {"explanation_id": explanation_id, "status": row.status}

    try:
        # ── Step 3: mark running ──────────────────────────────────────────
        row.status = AnalyticsExplanationStatus.running.value
        row.started_at = datetime.now(UTC)
        db.session.flush()

        # ── Step 4: re-build input from live DB state ─────────────────────
        # Never trust stale ORM state (AGENTS.md §2.4).
        post: Post | None = db.session.get(Post, row.prompt_post_id)
        if post is None:
            raise RuntimeError(
                f"Post {row.prompt_post_id} not found for explanation {explanation_id}."
            )

        workspace: Workspace | None = None
        if row.workspace_id is not None:
            workspace = db.session.get(Workspace, row.workspace_id)
            if workspace is None:
                raise RuntimeError(
                    f"Workspace {row.workspace_id} not found for "
                    f"explanation {explanation_id}."
                )

        from backend.services.prompt_analytics_explain_service import (  # noqa: PLC0415
            build_input,
        )

        input_dict = build_input(post, workspace, row.kind)

        # ── Step 5: truncate overall payload ─────────────────────────────
        max_chars: int = current_app.config.get("AI_MAX_INPUT_CHARS", 32768)
        serialised = json.dumps(input_dict, sort_keys=True, default=str)
        if len(serialised) > max_chars:
            # Truncate the diff field if present, then re-serialise.
            if "diff" in input_dict:
                overage = len(serialised) - max_chars
                input_dict["diff"] = input_dict["diff"][: max(0, len(input_dict["diff"]) - overage)]
            serialised = json.dumps(input_dict, sort_keys=True, default=str)
            serialised = serialised[:max_chars]
            input_dict = json.loads(serialised)

        # ── Step 6: call provider ─────────────────────────────────────────
        from backend.ai.providers import get_provider  # noqa: PLC0415

        provider = get_provider(current_app.config)
        explanation_md: str = provider.run_explanation(input_dict, row.kind)

        # ── Step 7: cancel guard ──────────────────────────────────────────
        # Re-fetch to detect any intervening status change (e.g. future cancel
        # support).  expire() forces SQLAlchemy to reload the row on next access.
        db.session.expire(row)
        row = db.session.get(AnalyticsExplanation, explanation_id)
        if row is None or row.status not in (
            AnalyticsExplanationStatus.running.value,
        ):
            db.session.rollback()
            status = row.status if row is not None else "deleted"
            return {"explanation_id": explanation_id, "status": status}

        # ── Step 8: truncate output ───────────────────────────────────────
        explanation_md = explanation_md[:_MAX_OUTPUT_CHARS]

        # ── Step 9: mark completed ────────────────────────────────────────
        row.status = AnalyticsExplanationStatus.completed.value
        row.explanation_md = explanation_md
        row.completed_at = datetime.now(UTC)
        row.error_message = None

        # ── Step 10: commit ───────────────────────────────────────────────
        db.session.commit()

        return {
            "explanation_id": row.id,
            "status": AnalyticsExplanationStatus.completed.value,
        }

    except Exception as exc:  # noqa: BLE001
        # ── Failure path ──────────────────────────────────────────────────
        db.session.rollback()

        # Reload after rollback — the row state was lost.
        row = db.session.get(AnalyticsExplanation, explanation_id)
        if row is not None:
            row.status = AnalyticsExplanationStatus.failed.value
            row.error_message = str(exc)[:_MAX_ERROR_CHARS]
            try:
                db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()

        # Exponential backoff: 10 s → 40 s → 160 s.
        countdown = 10 * (4 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
