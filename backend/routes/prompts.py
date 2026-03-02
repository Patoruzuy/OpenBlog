"""SSR — Prompt Library routes.

URL structure
-------------
Public layer:
  GET  /prompts/                         list published public prompts
  GET  /prompts/<slug>                   prompt detail page
  GET  /prompts/<slug>/analytics         prompt evolution analytics (public)
  GET  /prompts/new                      creation form        [auth required]
  POST /prompts/new                      create prompt        [auth required]

Workspace layer:
  GET  /w/<ws_slug>/prompts/             list workspace prompts   [member+]
  GET  /w/<ws_slug>/prompts/<slug>       prompt detail            [member+]
  GET  /w/<ws_slug>/prompts/<slug>/analytics  prompt analytics    [member+]
  GET  /w/<ws_slug>/prompts/new          creation form            [editor+]
  POST /w/<ws_slug>/prompts/new          create prompt            [editor+]

Permission rules
----------------
Public prompts:
  - Listing + detail: anyone (including unauthenticated)
  - Creation: any authenticated user (mirrors post creation)

Workspace prompts:
  - Listing + detail: workspace members (viewer+)
  - Creation: workspace editors (editor+)
  - Non-members → 404 (never 403; fail-closed like workspace.py)

Cache policy
------------
Workspace responses carry ``Cache-Control: private, no-store`` via an
after_request hook on the workspace sub-section of this blueprint.
Public prompt pages are publicly cacheable (max-age=60).
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from backend.extensions import db
from backend.models.post import PostStatus
from backend.models.workspace import WorkspaceMemberRole
from backend.services import workspace_service as ws_svc
from backend.services.prompt_service import (
    PromptError,
    create_prompt,
    get_prompt_by_slug,
    get_prompt_metadata,
    list_prompts,
    parsed_variables,
)
from backend.utils.auth import get_current_user, require_auth

prompts_bp = Blueprint("prompts", __name__)

_PER_PAGE = 20


# ── Public routes ─────────────────────────────────────────────────────────────


@prompts_bp.get("/prompts/")
def public_prompt_list():
    """List published public prompts."""
    page = max(1, request.args.get("page", 1, type=int))
    category = request.args.get("category") or None
    offset = (page - 1) * _PER_PAGE

    prompts = list_prompts(
        workspace_id=None,
        status="published",
        category=category,
        limit=_PER_PAGE + 1,  # peek ahead for 'has_next'
        offset=offset,
    )
    has_next = len(prompts) > _PER_PAGE
    prompts = prompts[:_PER_PAGE]

    return render_template(
        "prompts/list.html",
        prompts=prompts,
        page=page,
        has_next=has_next,
        category=category,
        workspace=None,
    )


@prompts_bp.route("/prompts/new", methods=["GET", "POST"])
@require_auth
def public_prompt_new():
    """Create a new public prompt (draft or published immediately)."""
    user = get_current_user()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        markdown_body = request.form.get("markdown_body", "").strip()
        category = request.form.get("category", "").strip()
        intended_model = request.form.get("intended_model", "").strip() or None
        usage_notes = request.form.get("usage_notes", "").strip() or None
        example_input = request.form.get("example_input", "").strip() or None
        example_output = request.form.get("example_output", "").strip() or None
        complexity_level = request.form.get("complexity_level", "intermediate").strip()
        variables_raw = request.form.get("variables_json", "{}").strip() or "{}"
        seo_description = request.form.get("seo_description", "").strip() or None
        action = request.form.get("action", "draft")
        status = PostStatus.published if action == "publish" else PostStatus.draft

        try:
            post = create_prompt(
                title=title,
                markdown_body=markdown_body,
                author=user,
                workspace_id=None,
                category=category,
                intended_model=intended_model,
                variables=variables_raw,
                usage_notes=usage_notes,
                example_input=example_input,
                example_output=example_output,
                complexity_level=complexity_level,
                status=status,
                seo_description=seo_description,
            )
            db.session.commit()
            flash("Prompt created.", "success")
            return redirect(url_for("prompts.public_prompt_detail", slug=post.slug))
        except PromptError as exc:
            flash(str(exc), "error")
            return render_template(
                "prompts/new.html", form_data=request.form, workspace=None
            )

    return render_template("prompts/new.html", form_data={}, workspace=None)


@prompts_bp.get("/prompts/<slug>")
def public_prompt_detail(slug: str):
    """Public prompt detail page."""
    prompt = get_prompt_by_slug(slug, workspace_id=None)
    if prompt is None or prompt.status != PostStatus.published:
        abort(404)

    meta = get_prompt_metadata(prompt.id)
    variables = parsed_variables(meta) if meta else {}

    user = get_current_user()

    from backend.models.user import UserRole  # noqa: PLC0415
    from backend.services.content_link_service import (
        list_links_grouped,  # noqa: PLC0415
    )
    from backend.services.content_link_suggestion_service import (  # noqa: PLC0415
        suggest_for_post,
    )

    links_grouped = list_links_grouped(prompt, workspace_id=None)
    can_manage_links = (
        user is not None and user.role in (UserRole.editor, UserRole.admin)
    )
    link_suggestions = suggest_for_post(user, prompt, workspace_id=None)

    return render_template(
        "prompts/detail.html",
        prompt=prompt,
        meta=meta,
        variables=variables,
        workspace=None,
        current_user=user,
        links_grouped=links_grouped,
        can_manage_links=can_manage_links,
        link_suggestions=link_suggestions,
        from_post=prompt,
    )


# ── Workspace routes ──────────────────────────────────────────────────────────


def _ws_no_store(response):
    """Enforce private, no-store on workspace prompt responses."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers.pop("Expires", None)
    response.headers.pop("Pragma", None)
    return response


@prompts_bp.get("/prompts/<slug>/analytics")
def public_prompt_analytics(slug: str):
    """Prompt evolution analytics page — public scope."""
    prompt = get_prompt_by_slug(slug, workspace_id=None)
    if prompt is None or prompt.status != PostStatus.published:
        abort(404)

    from backend.services.prompt_analytics_service import (  # noqa: PLC0415
        get_execution_stats,
        get_fork_tree,
        get_rating_trend,
        get_version_timeline,
    )

    timeline = get_version_timeline(prompt, workspace_id=None)
    rating_trend = get_rating_trend(prompt, workspace_id=None)
    forks = get_fork_tree(prompt, workspace_id=None)
    exec_stats = get_execution_stats(prompt, workspace_id=None)

    return render_template(
        "prompts/analytics.html",
        prompt=prompt,
        workspace=None,
        timeline=timeline,
        rating_trend=rating_trend,
        forks=forks,
        exec_stats=exec_stats,
        current_user=get_current_user(),
    )


@prompts_bp.get("/w/<ws_slug>/prompts/")
def ws_prompt_list(ws_slug: str):
    """List prompts visible to the current workspace member."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)

    prompts = list_prompts(workspace_id=ws.id, limit=100)

    resp = make_response(
        render_template(
            "prompts/list.html",
            prompts=prompts,
            page=1,
            has_next=False,
            category=None,
            workspace=ws,
        )
    )
    return _ws_no_store(resp)


@prompts_bp.get("/w/<ws_slug>/prompts/<slug>/analytics")
def ws_prompt_analytics(ws_slug: str, slug: str):
    """Prompt evolution analytics page — workspace scope (member+)."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)

    prompt = get_prompt_by_slug(slug, workspace_id=ws.id)
    if prompt is None:
        abort(404)

    from backend.services.prompt_analytics_service import (  # noqa: PLC0415
        get_execution_stats,
        get_fork_tree,
        get_rating_trend,
        get_version_timeline,
    )

    timeline = get_version_timeline(prompt, workspace_id=ws.id)
    rating_trend = get_rating_trend(prompt, workspace_id=ws.id)
    forks = get_fork_tree(prompt, workspace_id=ws.id)
    exec_stats = get_execution_stats(prompt, workspace_id=ws.id)

    resp = make_response(
        render_template(
            "prompts/analytics.html",
            prompt=prompt,
            workspace=ws,
            timeline=timeline,
            rating_trend=rating_trend,
            forks=forks,
            exec_stats=exec_stats,
            current_user=user,
        )
    )
    return _ws_no_store(resp)


@prompts_bp.route("/w/<ws_slug>/prompts/new", methods=["GET", "POST"])
def ws_prompt_new(ws_slug: str):
    """Create a workspace-scoped prompt (editor+)."""
    user = get_current_user()
    if user is None:
        return redirect(url_for("auth.login", next=request.path))

    ws = ws_svc.get_workspace_for_user(
        ws_slug, user, required_role=WorkspaceMemberRole.editor
    )

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        markdown_body = request.form.get("markdown_body", "").strip()
        category = request.form.get("category", "").strip()
        intended_model = request.form.get("intended_model", "").strip() or None
        usage_notes = request.form.get("usage_notes", "").strip() or None
        example_input = request.form.get("example_input", "").strip() or None
        example_output = request.form.get("example_output", "").strip() or None
        complexity_level = request.form.get("complexity_level", "intermediate").strip()
        variables_raw = request.form.get("variables_json", "{}").strip() or "{}"
        seo_description = request.form.get("seo_description", "").strip() or None
        action = request.form.get("action", "draft")
        status = PostStatus.published if action == "publish" else PostStatus.draft

        try:
            post = create_prompt(
                title=title,
                markdown_body=markdown_body,
                author=user,
                workspace_id=ws.id,
                category=category,
                intended_model=intended_model,
                variables=variables_raw,
                usage_notes=usage_notes,
                example_input=example_input,
                example_output=example_output,
                complexity_level=complexity_level,
                status=status,
                seo_description=seo_description,
            )
            db.session.commit()
            flash("Prompt created.", "success")
            resp = make_response(
                redirect(
                    url_for(
                        "prompts.ws_prompt_detail",
                        ws_slug=ws_slug,
                        slug=post.slug,
                    )
                )
            )
            return _ws_no_store(resp)
        except PromptError as exc:
            flash(str(exc), "error")
            resp = make_response(
                render_template(
                    "prompts/new.html",
                    form_data=request.form,
                    workspace=ws,
                )
            )
            return _ws_no_store(resp)

    resp = make_response(
        render_template("prompts/new.html", form_data={}, workspace=ws)
    )
    return _ws_no_store(resp)


@prompts_bp.get("/w/<ws_slug>/prompts/<slug>")
def ws_prompt_detail(ws_slug: str, slug: str):
    """Workspace prompt detail page (member+)."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)

    prompt = get_prompt_by_slug(slug, workspace_id=ws.id)
    if prompt is None:
        abort(404)

    meta = get_prompt_metadata(prompt.id)
    variables = parsed_variables(meta) if meta else {}

    from backend.services.content_link_service import (
        _can_manage,  # noqa: PLC0415
        list_links_grouped,  # noqa: PLC0415
    )
    from backend.services.content_link_suggestion_service import (  # noqa: PLC0415
        suggest_for_post,
    )

    links_grouped = list_links_grouped(prompt, workspace_id=ws.id)
    can_manage_links = user is not None and _can_manage(user, ws.id)
    link_suggestions = suggest_for_post(user, prompt, workspace_id=ws.id)

    resp = make_response(
        render_template(
            "prompts/detail.html",
            prompt=prompt,
            meta=meta,
            variables=variables,
            workspace=ws,
            current_user=user,
            links_grouped=links_grouped,
            can_manage_links=can_manage_links,
            link_suggestions=link_suggestions,
            from_post=prompt,
        )
    )
    return _ws_no_store(resp)
