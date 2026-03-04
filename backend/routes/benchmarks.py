"""SSR — Benchmark Suite routes.

URL structure
-------------
Public layer (public suites only):
  GET  /benchmarks/                       list public suites     [auth required]
  GET  /benchmarks/<slug>                 suite detail            [auth required]
  POST /benchmarks/<slug>/run             start a run            [auth required]
  GET  /benchmarks/runs/<run_id>          run result detail      [auth required]

Workspace layer:
  GET  /w/<ws>/benchmarks/               list workspace suites  [member+]
  GET  /w/<ws>/benchmarks/<slug>         suite detail           [member+]
  POST /w/<ws>/benchmarks/<slug>/run     start a run            [member+]
  GET  /w/<ws>/benchmarks/runs/<run_id>  run result detail      [member+]

Permission rules
----------------
- Non-authenticated → 302 redirect to /login.
- Non-member on workspace routes → 404 (fail-closed).
- Scope violations (wrong workspace prompt) → 422 flash + redirect.

Cache policy
------------
Workspace responses carry ``Cache-Control: private, no-store``.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from backend.extensions import db
from backend.services import benchmark_service as bsvc
from backend.services import workspace_service as ws_svc
from backend.services.benchmark_service import BenchmarkError
from backend.utils.auth import get_current_user, require_auth

benchmarks_bp = Blueprint("benchmarks", __name__)


def _ws_no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


# ── Public routes ─────────────────────────────────────────────────────────────


@benchmarks_bp.get("/benchmarks/")
@require_auth
def public_suite_list():
    """List public benchmark suites."""
    user = get_current_user()
    suites = bsvc.list_suites(user, workspace=None)
    return render_template(
        "benchmarks/list.html",
        suites=suites,
        workspace=None,
        current_user=user,
    )


@benchmarks_bp.get("/benchmarks/<slug>")
@require_auth
def public_suite_detail(slug: str):
    """Show a public benchmark suite with its cases and run history."""
    user = get_current_user()
    suite = bsvc.get_suite(user, slug, workspace=None)
    if suite is None:
        abort(404)
    return render_template(
        "benchmarks/detail.html",
        suite=suite,
        workspace=None,
        current_user=user,
    )


@benchmarks_bp.post("/benchmarks/<slug>/run")
@require_auth
def public_suite_run(slug: str):
    """Enqueue a benchmark run for a public suite."""
    user = get_current_user()
    suite = bsvc.get_suite(user, slug, workspace=None)
    if suite is None:
        abort(404)

    prompt_post_id = request.form.get("prompt_post_id", type=int)
    version = request.form.get("version", type=int)
    model_name = request.form.get("model_name", "").strip() or None

    if not prompt_post_id or not version:
        flash("Prompt and version are required.", "error")
        return redirect(url_for("benchmarks.public_suite_detail", slug=slug))

    from backend.models.post import Post  # noqa: PLC0415

    prompt = db.session.get(Post, prompt_post_id)
    if prompt is None:
        abort(404)

    try:
        run = bsvc.create_run(user, suite, prompt, version, model_name)
        db.session.commit()
        flash("Benchmark run queued.", "success")
        return redirect(url_for("benchmarks.public_run_detail", run_id=run.id))
    except BenchmarkError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("benchmarks.public_suite_detail", slug=slug))


@benchmarks_bp.get("/benchmarks/runs/<int:run_id>")
@require_auth
def public_run_detail(run_id: int):
    """Show the result detail for a public benchmark run."""
    user = get_current_user()
    run = bsvc.get_run_with_results(user, run_id)
    if run is None:
        abort(404)
    # Prevent workspace runs from leaking through public route.
    if run.workspace_id is not None:
        abort(404)
    return render_template(
        "benchmarks/run_detail.html",
        run=run,
        workspace=None,
        current_user=user,
    )


# ── Workspace routes ──────────────────────────────────────────────────────────


@benchmarks_bp.after_request
def _add_no_store_for_workspace(response):
    """Apply private, no-store to all /w/<ws>/benchmarks/* responses."""
    if request.path.startswith("/w/"):
        return _ws_no_store(response)
    return response


@benchmarks_bp.get("/w/<ws_slug>/benchmarks/")
@require_auth
def ws_suite_list(ws_slug: str):
    """List benchmark suites in a workspace."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    suites = bsvc.list_suites(user, workspace=workspace)
    return render_template(
        "benchmarks/list.html",
        suites=suites,
        workspace=workspace,
        current_user=user,
    )


@benchmarks_bp.get("/w/<ws_slug>/benchmarks/<slug>")
@require_auth
def ws_suite_detail(ws_slug: str, slug: str):
    """Show a workspace benchmark suite."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    suite = bsvc.get_suite(user, slug, workspace=workspace)
    if suite is None:
        abort(404)
    return render_template(
        "benchmarks/detail.html",
        suite=suite,
        workspace=workspace,
        current_user=user,
    )


@benchmarks_bp.post("/w/<ws_slug>/benchmarks/<slug>/run")
@require_auth
def ws_suite_run(ws_slug: str, slug: str):
    """Enqueue a benchmark run for a workspace suite."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    suite = bsvc.get_suite(user, slug, workspace=workspace)
    if suite is None:
        abort(404)

    prompt_post_id = request.form.get("prompt_post_id", type=int)
    version = request.form.get("version", type=int)
    model_name = request.form.get("model_name", "").strip() or None

    if not prompt_post_id or not version:
        flash("Prompt and version are required.", "error")
        return redirect(
            url_for("benchmarks.ws_suite_detail", ws_slug=ws_slug, slug=slug)
        )

    from backend.models.post import Post  # noqa: PLC0415

    prompt = db.session.get(Post, prompt_post_id)
    if prompt is None:
        abort(404)

    try:
        run = bsvc.create_run(user, suite, prompt, version, model_name)
        db.session.commit()
        flash("Benchmark run queued.", "success")
        return redirect(
            url_for("benchmarks.ws_run_detail", ws_slug=ws_slug, run_id=run.id)
        )
    except BenchmarkError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for("benchmarks.ws_suite_detail", ws_slug=ws_slug, slug=slug)
        )


@benchmarks_bp.get("/w/<ws_slug>/benchmarks/runs/<int:run_id>")
@require_auth
def ws_run_detail(ws_slug: str, run_id: int):
    """Show the result detail for a workspace benchmark run."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    run = bsvc.get_run_with_results(user, run_id)
    if run is None:
        abort(404)
    # Prevent public runs from leaking through workspace route.
    if run.workspace_id != workspace.id:
        abort(404)
    return render_template(
        "benchmarks/run_detail.html",
        run=run,
        workspace=workspace,
        current_user=user,
    )
