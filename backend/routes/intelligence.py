"""SSR — Intelligence Dashboard routes.

URL structure
-------------
  GET  /intelligence          Public cross-family benchmark intelligence view.
  GET  /w/<ws>/intelligence   Workspace-scoped intelligence view.

Permission rules
----------------
- Public route: no authentication required (read-only, public data).
- Workspace route: non-member → 404 (fail-closed, via get_workspace_for_user).

Cache policy
------------
Workspace responses carry ``Cache-Control: private, no-store``.
Public responses carry no special cache header.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from backend.services import intelligence_service as intel_svc
from backend.services import workspace_service as ws_svc
from backend.utils.auth import get_current_user

intelligence_bp = Blueprint("intelligence", __name__)


def _ws_no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers.pop("Expires", None)
    response.headers.pop("Pragma", None)
    return response


@intelligence_bp.after_request
def _add_no_store_for_workspace(response):
    if request.path.startswith("/w/"):
        return _ws_no_store(response)
    return response


# ── Public route ──────────────────────────────────────────────────────────────


@intelligence_bp.get("/intelligence")
def public_intelligence():
    """Render the public cross-family benchmark intelligence dashboard."""
    top_prompts = intel_svc.get_top_prompts(workspace=None)
    most_improved = intel_svc.get_most_improved(workspace=None)
    ontology_performance = intel_svc.get_ontology_performance(workspace=None)
    fork_outperformance = intel_svc.get_fork_outperformance(workspace=None)

    return render_template(
        "intelligence.html",
        workspace=None,
        top_prompts=top_prompts,
        most_improved=most_improved,
        ontology_performance=ontology_performance,
        fork_outperformance=fork_outperformance,
    )


# ── Workspace route ──────────────────────────────────────────────────────────


@intelligence_bp.get("/w/<ws_slug>/intelligence")
def ws_intelligence(ws_slug: str):
    """Render the workspace-scoped intelligence dashboard."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)

    top_prompts = intel_svc.get_top_prompts(workspace=ws)
    most_improved = intel_svc.get_most_improved(workspace=ws)
    ontology_performance = intel_svc.get_ontology_performance(workspace=ws)
    fork_outperformance = intel_svc.get_fork_outperformance(workspace=ws)

    return render_template(
        "intelligence.html",
        workspace=ws,
        top_prompts=top_prompts,
        most_improved=most_improved,
        ontology_performance=ontology_performance,
        fork_outperformance=fork_outperformance,
    )
