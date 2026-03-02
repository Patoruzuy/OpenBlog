"""Celery task — execute a benchmark run.

Task name: ``tasks.run_benchmark``

Behaviour
---------
1.  Load the BenchmarkRun row.
2.  Guard: skip if status is not ``queued`` (prevents double-execution).
3.  Mark status → ``running``, flush.
4.  Load benchmark cases for the suite (one query, bounded).
5.  Load the prompt version body from PostVersion (falls back to Post.markdown_body).
6.  For each case:
    a.  Check run.status — if `canceled`, stop immediately (no further cases).
    b.  Render the prompt template by substituting case.input_json variables.
    c.  Call a mock provider (MVP — real LLM call is Phase 2).
    d.  Upsert a BenchmarkRunResult row.
7.  Mark status → ``completed``, set completed_at.
8.  Commit.

On exception:
- status → ``failed``
- error_message truncated to 400 chars
- Commit.
- Re-raise (no retry in MVP; add retry with backoff in Phase 2).

Cancellation guard
------------------
Between processing each case the task reloads run.status from the DB.
If it has been set to ``canceled`` by ``cancel_run()``, the loop exits
early and the run is left in ``canceled`` state (not overwritten to
``completed``).
"""

from __future__ import annotations

from celery import shared_task

_MAX_ERROR_MSG = 400


@shared_task(bind=True, name="tasks.run_benchmark")
def run_benchmark(self, run_id: int) -> dict:  # type: ignore[override]  # noqa: ARG002
    """Execute benchmark run *run_id* and store results for every case.

    Parameters
    ----------
    run_id:
        Primary key of the :class:`~backend.models.benchmark.BenchmarkRun` to process.

    Returns
    -------
    dict
        ``{"run_id": int, "status": str, "results": int}``
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.benchmark import (  # noqa: PLC0415
        BenchmarkCase,
        BenchmarkRun,
        BenchmarkRunResult,
        BenchmarkRunStatus,
    )
    from backend.models.post import Post  # noqa: PLC0415
    from backend.models.post_version import PostVersion  # noqa: PLC0415

    run: BenchmarkRun | None = db.session.get(BenchmarkRun, run_id)
    if run is None:
        return {"run_id": run_id, "status": "not_found", "results": 0}

    # Guard: skip if already processed (duplicate delivery / eager re-run).
    if run.status != BenchmarkRunStatus.queued.value:
        return {"run_id": run_id, "status": run.status, "results": 0}

    try:
        # ── Step 3: mark running ──────────────────────────────────────
        run.status = BenchmarkRunStatus.running.value
        run.started_at = datetime.now(UTC)
        db.session.flush()

        # ── Step 4: load cases (bounded — one query) ──────────────────
        cases: list[BenchmarkCase] = list(
            db.session.scalars(
                select(BenchmarkCase)
                .where(BenchmarkCase.suite_id == run.suite_id)
                .order_by(BenchmarkCase.id)
            ).all()
        )

        # ── Step 5: load prompt body for this version ─────────────────
        pv: PostVersion | None = db.session.scalar(
            select(PostVersion).where(
                PostVersion.post_id == run.prompt_post_id,
                PostVersion.version_number == run.prompt_version,
            )
        )
        if pv is not None:
            prompt_body = pv.markdown_body
        else:
            post: Post | None = db.session.get(Post, run.prompt_post_id)
            prompt_body = post.markdown_body if post is not None else ""

        results_stored = 0

        # ── Step 6: process each case ─────────────────────────────────
        for case in cases:
            # Re-check cancellation from DB before each case.
            db.session.refresh(run)
            if run.status == BenchmarkRunStatus.canceled.value:
                return {"run_id": run_id, "status": "canceled", "results": results_stored}

            # Render prompt by simple variable substitution.
            rendered = _render_prompt(prompt_body, case.input_json)

            # MVP mock provider — returns a placeholder output.
            output_text = _mock_provider(rendered, run.model_name)

            # Upsert result row.
            existing = db.session.scalar(
                select(BenchmarkRunResult).where(
                    BenchmarkRunResult.run_id == run_id,
                    BenchmarkRunResult.case_id == case.id,
                )
            )
            if existing is None:
                result_row = BenchmarkRunResult(
                    run_id=run_id,
                    case_id=case.id,
                    output_text=output_text,
                    created_at=datetime.now(UTC),
                )
                db.session.add(result_row)
            else:
                existing.output_text = output_text
            db.session.flush()
            results_stored += 1

        # ── Step 7: mark completed ────────────────────────────────────
        run.status = BenchmarkRunStatus.completed.value
        run.completed_at = datetime.now(UTC)
        db.session.commit()
        return {"run_id": run_id, "status": "completed", "results": results_stored}

    except Exception as exc:
        # Roll back partial writes before re-marking.
        db.session.rollback()
        try:
            run_retry: BenchmarkRun | None = db.session.get(BenchmarkRun, run_id)
            if run_retry is not None:
                run_retry.status = BenchmarkRunStatus.failed.value
                run_retry.error_message = str(exc)[:_MAX_ERROR_MSG]
                run_retry.completed_at = datetime.now(UTC)
                db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()
        raise


def _render_prompt(template: str, variables: dict) -> str:
    """Substitute ``{{variable}}`` placeholders in *template* with *variables*.

    Falls back to leaving the placeholder unchanged if the key is missing.
    This mirrors the existing prompt variable substitution used in the UI.
    """
    result = template
    for key, value in (variables or {}).items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
    return result


def _mock_provider(rendered_prompt: str, model_name: str | None) -> str:  # noqa: ARG001
    """MVP placeholder — returns a mock response for testing.

    Phase 2 will replace this with real provider dispatch based on model_name.
    """
    preview = rendered_prompt[:80].replace("\n", " ")
    return f"[mock output for: {preview}]"
