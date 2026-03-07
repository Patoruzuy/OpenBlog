"""SSR — Leaderboard routes.

URL structure
-------------
GET  /leaderboard                                        public leaderboard
GET  /ontology/<slug>/leaderboard                        public ontology leaderboard
GET  /w/<ws_slug>/leaderboard                            workspace leaderboard  [member]
GET  /w/<ws_slug>/ontology/<slug>/leaderboard            workspace ontology lb  [member]

Cache policy
------------
Public routes:   ``Cache-Control: public, max-age=120``
Workspace routes: ``Cache-Control: private, no-store``  (set inline per-route
                  so the header does not bleed onto public routes in this blueprint)

Scope enforcement
-----------------
All ranking and filtering is performed in SQL inside :mod:`leaderboard_service`.
Routes are thin: they gate membership, look up models, and render templates.
"""

from __future__ import annotations

from flask import Blueprint, abort, make_response, render_template

from backend.services import ontology_service
from backend.services import workspace_service as ws_svc
from backend.services.leaderboard_service import LeaderboardService
from backend.utils.auth import get_current_user, require_auth

leaderboard_bp = Blueprint("leaderboard", __name__)


# ── Public leaderboard ────────────────────────────────────────────────────────


@leaderboard_bp.get("/leaderboard")
def public_leaderboard():
    """Rank all users by public reputation."""
    rows = LeaderboardService.get_public_leaderboard(limit=50)
    resp = make_response(
        render_template(
            "leaderboards/index.html",
            rows=rows,
            title="Public Leaderboard",
            scope_label="Public reputation",
        )
    )
    resp.headers["Cache-Control"] = "public, max-age=120"
    return resp


# ── Public ontology leaderboard ───────────────────────────────────────────────


@leaderboard_bp.get("/ontology/<slug>/leaderboard")
def public_ontology_leaderboard(slug: str):
    """Rank contributors within a public ontology node (descendants included)."""
    node = ontology_service.get_node_by_slug(slug)
    if node is None or not node.is_public:
        abort(404)

    rows = LeaderboardService.get_public_ontology_leaderboard(node, limit=50)
    resp = make_response(
        render_template(
            "leaderboards/ontology.html",
            rows=rows,
            node=node,
            title=f"{node.name} — Leaderboard",
            scope_label="Public reputation",
        )
    )
    resp.headers["Cache-Control"] = "public, max-age=120"
    return resp


# ── Workspace leaderboard ─────────────────────────────────────────────────────


@leaderboard_bp.get("/w/<ws_slug>/leaderboard")
@require_auth
def workspace_leaderboard(ws_slug: str):
    """Rank workspace members by workspace reputation. Non-members get 404."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)

    rows = LeaderboardService.get_workspace_leaderboard(workspace, limit=50)
    resp = make_response(
        render_template(
            "leaderboards/index.html",
            rows=rows,
            title=f"{workspace.name} — Leaderboard",
            scope_label="Workspace reputation",
        )
    )
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


# ── Workspace ontology leaderboard ────────────────────────────────────────────


@leaderboard_bp.get("/w/<ws_slug>/ontology/<slug>/leaderboard")
@require_auth
def workspace_ontology_leaderboard(ws_slug: str, slug: str):
    """Rank workspace members within an ontology node. Non-members get 404."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(ws_slug, user)

    node = ontology_service.get_node_by_slug(slug)
    if node is None:
        abort(404)

    rows = LeaderboardService.get_workspace_ontology_leaderboard(
        workspace, node, limit=50
    )
    resp = make_response(
        render_template(
            "leaderboards/ontology.html",
            rows=rows,
            node=node,
            title=f"{node.name} — {workspace.name} Leaderboard",
            scope_label="Workspace reputation",
        )
    )
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
