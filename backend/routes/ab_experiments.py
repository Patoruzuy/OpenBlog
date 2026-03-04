"""SSR — A/B Experiment routes.

URL structure
-------------
Public layer:
  GET  /ab                       list public experiments          [auth required]
  GET  /ab/new                   new experiment form              [auth required]
  POST /ab/new                   create experiment                [auth required]
  GET  /ab/<slug>                experiment detail                [auth required]
  POST /ab/<slug>/start          start experiment                 [auth required]
  POST /ab/<slug>/cancel         cancel experiment                [auth required]

Workspace layer:
  GET  /w/<ws>/ab                list workspace experiments       [member+]
  GET  /w/<ws>/ab/new            new experiment form              [member+]
  POST /w/<ws>/ab/new            create experiment                [member+]
  GET  /w/<ws>/ab/<slug>         experiment detail                [member+]
  POST /w/<ws>/ab/<slug>/start   start experiment                 [member+]
  POST /w/<ws>/ab/<slug>/cancel  cancel experiment                [member+]

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
from backend.services import ab_experiment_service as ab_svc
from backend.services import workspace_service as ws_svc
from backend.services.benchmark_service import BenchmarkError
from backend.utils.auth import get_current_user, require_auth

ab_bp = Blueprint("ab", __name__)


def _ws_no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


@ab_bp.after_request
def _add_no_store_for_workspace(response):
    if request.path.startswith("/w/"):
        return _ws_no_store(response)
    return response


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_form_posts(form):
    """Return (variant_a_prompt, variant_a_version, variant_b_prompt, variant_b_version)
    from form data, or raise ValueError with a user-friendly message."""
    from backend.models.post import Post  # noqa: PLC0415

    a_id = form.get("variant_a_prompt_post_id", type=int)
    a_ver = form.get("variant_a_version", type=int)
    b_id = form.get("variant_b_prompt_post_id", type=int)
    b_ver = form.get("variant_b_version", type=int)

    if not all([a_id, a_ver, b_id, b_ver]):
        raise ValueError("All variant fields (prompt IDs and versions) are required.")

    prompt_a = db.session.get(Post, a_id)
    prompt_b = db.session.get(Post, b_id)
    if prompt_a is None or prompt_b is None:
        raise ValueError("One or both prompt posts not found.")

    return prompt_a, a_ver, prompt_b, b_ver


# ── Public routes ─────────────────────────────────────────────────────────────


@ab_bp.get("/ab")
@require_auth
def public_list():
    user = get_current_user()
    experiments = ab_svc.list_experiments(user, workspace=None)
    return render_template(
        "ab/list.html",
        experiments=experiments,
        workspace=None,
        current_user=user,
    )


@ab_bp.get("/ab/new")
@require_auth
def public_new():
    from sqlalchemy import select  # noqa: PLC0415

    from backend.models.benchmark import BenchmarkSuite  # noqa: PLC0415

    user = get_current_user()
    suites = db.session.scalars(
        select(BenchmarkSuite)
        .where(BenchmarkSuite.workspace_id.is_(None))
        .order_by(BenchmarkSuite.name)
    ).all()
    return render_template(
        "ab/new.html",
        suites=suites,
        workspace=None,
        current_user=user,
    )


@ab_bp.post("/ab/new")
@require_auth
def public_new_post():
    user = get_current_user()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    suite_id = request.form.get("suite_id", type=int)

    from backend.models.benchmark import BenchmarkSuite  # noqa: PLC0415

    suite = db.session.get(BenchmarkSuite, suite_id) if suite_id else None
    if suite is None:
        flash("Suite is required.", "error")
        return redirect(url_for("ab.public_new"))

    try:
        prompt_a, ver_a, prompt_b, ver_b = _load_form_posts(request.form)
        exp = ab_svc.create_experiment(
            user,
            name,
            suite,
            prompt_a,
            ver_a,
            prompt_b,
            ver_b,
            description=description,
        )
        db.session.commit()
        flash("Experiment created.", "success")
        return redirect(url_for("ab.public_detail", slug=exp.slug))
    except (ValueError, BenchmarkError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("ab.public_new"))


@ab_bp.get("/ab/<slug>")
@require_auth
def public_detail(slug: str):
    user = get_current_user()
    exp = ab_svc.get_experiment(user, slug, workspace=None)
    if exp is None:
        abort(404)
    comparison = ab_svc.compute_comparison(user, exp)
    db.session.commit()  # persist any status sync
    return render_template(
        "ab/detail.html",
        exp=exp,
        comparison=comparison,
        workspace=None,
        current_user=user,
    )


@ab_bp.post("/ab/<slug>/start")
@require_auth
def public_start(slug: str):
    user = get_current_user()
    exp = ab_svc.get_experiment(user, slug, workspace=None)
    if exp is None:
        abort(404)
    try:
        ab_svc.start_experiment(user, exp)
        db.session.commit()
        flash("Experiment started.", "success")
    except BenchmarkError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ab.public_detail", slug=slug))


@ab_bp.post("/ab/<slug>/cancel")
@require_auth
def public_cancel(slug: str):
    user = get_current_user()
    exp = ab_svc.get_experiment(user, slug, workspace=None)
    if exp is None:
        abort(404)
    try:
        ab_svc.cancel_experiment(user, exp)
        db.session.commit()
        flash("Experiment canceled.", "success")
    except BenchmarkError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ab.public_detail", slug=slug))


# ── Workspace routes ──────────────────────────────────────────────────────────


@ab_bp.get("/w/<ws_slug>/ab")
@require_auth
def ws_list(ws_slug: str):
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    experiments = ab_svc.list_experiments(user, workspace=workspace)
    return render_template(
        "ab/list.html",
        experiments=experiments,
        workspace=workspace,
        current_user=user,
    )


@ab_bp.get("/w/<ws_slug>/ab/new")
@require_auth
def ws_new(ws_slug: str):
    from sqlalchemy import select  # noqa: PLC0415

    from backend.models.benchmark import BenchmarkSuite  # noqa: PLC0415

    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    suites = db.session.scalars(
        select(BenchmarkSuite)
        .where(BenchmarkSuite.workspace_id == workspace.id)
        .order_by(BenchmarkSuite.name)
    ).all()
    return render_template(
        "ab/new.html",
        suites=suites,
        workspace=workspace,
        current_user=user,
    )


@ab_bp.post("/w/<ws_slug>/ab/new")
@require_auth
def ws_new_post(ws_slug: str):
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    suite_id = request.form.get("suite_id", type=int)

    from backend.models.benchmark import BenchmarkSuite  # noqa: PLC0415

    suite = db.session.get(BenchmarkSuite, suite_id) if suite_id else None
    if suite is None:
        flash("Suite is required.", "error")
        return redirect(url_for("ab.ws_new", ws_slug=ws_slug))

    try:
        prompt_a, ver_a, prompt_b, ver_b = _load_form_posts(request.form)
        exp = ab_svc.create_experiment(
            user,
            name,
            suite,
            prompt_a,
            ver_a,
            prompt_b,
            ver_b,
            description=description,
            workspace=workspace,
        )
        db.session.commit()
        flash("Experiment created.", "success")
        return redirect(url_for("ab.ws_detail", ws_slug=ws_slug, slug=exp.slug))
    except (ValueError, BenchmarkError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("ab.ws_new", ws_slug=ws_slug))


@ab_bp.get("/w/<ws_slug>/ab/<slug>")
@require_auth
def ws_detail(ws_slug: str, slug: str):
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    exp = ab_svc.get_experiment(user, slug, workspace=workspace)
    if exp is None:
        abort(404)
    comparison = ab_svc.compute_comparison(user, exp)
    db.session.commit()
    return render_template(
        "ab/detail.html",
        exp=exp,
        comparison=comparison,
        workspace=workspace,
        current_user=user,
    )


@ab_bp.post("/w/<ws_slug>/ab/<slug>/start")
@require_auth
def ws_start(ws_slug: str, slug: str):
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    exp = ab_svc.get_experiment(user, slug, workspace=workspace)
    if exp is None:
        abort(404)
    try:
        ab_svc.start_experiment(user, exp)
        db.session.commit()
        flash("Experiment started.", "success")
    except BenchmarkError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ab.ws_detail", ws_slug=ws_slug, slug=slug))


@ab_bp.post("/w/<ws_slug>/ab/<slug>/cancel")
@require_auth
def ws_cancel(ws_slug: str, slug: str):
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)
    if workspace is None:
        abort(404)
    exp = ab_svc.get_experiment(user, slug, workspace=workspace)
    if exp is None:
        abort(404)
    try:
        ab_svc.cancel_experiment(user, exp)
        db.session.commit()
        flash("Experiment canceled.", "success")
    except BenchmarkError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ab.ws_detail", ws_slug=ws_slug, slug=slug))
