"""Ontology browse + mapping blueprint.

Route map
---------
Public browse:
  GET  /ontology                       list the public concept tree
  GET  /ontology/<slug>                node detail + prompts mapped to it

Workspace browse:
  GET  /w/<ws_slug>/ontology           tree with workspace overlay  [member+]
  GET  /w/<ws_slug>/ontology/<slug>    node detail with ws overlay  [member+]

Mapping (form POST; redirects back to the prompt):
  POST /prompts/<slug>/ontology        set public mappings          [editor+]
  POST /w/<ws_slug>/prompts/<slug>/ontology  set ws overlay         [ws editor+]

Cache policy
------------
Workspace routes carry ``Cache-Control: private, no-store``.
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
from backend.services import workspace_service as ws_svc
from backend.services.content_ontology_service import (
    ContentOntologyError,
    set_mappings,
)
from backend.services.ontology_service import (
    get_node_by_slug,
    list_prompts_for_node,
    list_tree,
)
from backend.services.prompt_service import get_prompt_by_slug
from backend.utils.auth import get_current_user, require_auth

ontology_bp = Blueprint("ontology", __name__)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _flat_tree(tree_items, *, depth: int = 0):
    """Yield (depth, NodeTreeItem) pairs in depth-first order."""
    for item in tree_items:
        yield depth, item
        yield from _flat_tree(item.children, depth=depth + 1)


# ── Public browse ─────────────────────────────────────────────────────────────


@ontology_bp.get("/ontology")
def public_ontology_index():
    """List the public concept tree."""
    tree = list_tree(public_only=True)
    return render_template(
        "ontology/index.html",
        tree=tree,
        workspace=None,
        flat_tree=list(_flat_tree(tree)),
    )


@ontology_bp.get("/ontology/<slug>")
def public_node_detail(slug: str):
    """Show a single concept node and the prompts mapped to it."""
    node = get_node_by_slug(slug)
    if node is None or not node.is_public:
        abort(404)

    user = get_current_user()
    prompts = list_prompts_for_node(
        user, node, workspace=None, include_descendants=True
    )
    return render_template(
        "ontology/detail.html",
        node=node,
        prompts=prompts,
        workspace=None,
        current_user=user,
    )


# ── Workspace browse ──────────────────────────────────────────────────────────


@ontology_bp.get("/w/<ws_slug>/ontology")
def ws_ontology_index(ws_slug: str):
    """List the concept tree with optional workspace overlay — members only."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)  # 404 if non-member

    tree = list_tree(public_only=True)
    resp = make_response(
        render_template(
            "ontology/index.html",
            tree=tree,
            workspace=ws,
            flat_tree=list(_flat_tree(tree)),
        )
    )
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@ontology_bp.get("/w/<ws_slug>/ontology/<slug>")
def ws_node_detail(ws_slug: str, slug: str):
    """Show node detail with workspace overlay — members only."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)  # 404 if non-member

    node = get_node_by_slug(slug)
    if node is None or not node.is_public:
        abort(404)

    prompts = list_prompts_for_node(
        user, node, workspace=ws, include_descendants=True
    )
    resp = make_response(
        render_template(
            "ontology/detail.html",
            node=node,
            prompts=prompts,
            workspace=ws,
            current_user=user,
        )
    )
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


# ── Mapping endpoints ─────────────────────────────────────────────────────────


@ontology_bp.post("/prompts/<slug>/ontology")
@require_auth
def set_public_mapping(slug: str):
    """Replace the public ontology mapping for a prompt."""
    user = get_current_user()
    prompt = get_prompt_by_slug(slug, workspace_id=None)
    if prompt is None or prompt.status != PostStatus.published:
        abort(404)

    node_ids = [int(x) for x in request.form.getlist("node_ids") if x.isdigit()]

    try:
        set_mappings(user, prompt, node_ids, workspace=None)
        db.session.commit()
        flash("Ontology mapping saved.", "success")
    except ContentOntologyError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("prompts.public_prompt_detail", slug=slug))


@ontology_bp.post("/w/<ws_slug>/prompts/<slug>/ontology")
@require_auth
def set_ws_mapping(ws_slug: str, slug: str):
    """Replace the workspace ontology overlay for a prompt."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)  # 404 if non-member

    prompt = get_prompt_by_slug(slug, workspace_id=ws.id)
    if prompt is None or prompt.status != PostStatus.published:
        abort(404)

    node_ids = [int(x) for x in request.form.getlist("node_ids") if x.isdigit()]

    try:
        set_mappings(user, prompt, node_ids, workspace=ws)
        db.session.commit()
        flash("Ontology mapping saved.", "success")
    except ContentOntologyError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(
        url_for("prompts.ws_prompt_detail", ws_slug=ws_slug, slug=slug)
    )
