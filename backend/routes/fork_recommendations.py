"""SSR routes — Optimal Fork Recommendations.

URL structure
-------------
Public:
  GET  /prompts/<slug>/recommendations

Workspace:
  GET  /w/<ws_slug>/prompts/<slug>/recommendations

Both routes render the same template with ``recommendations`` (a list of
:class:`~backend.services.fork_recommendation_service.ForkRecommendation`)
and the base ``prompt`` post.

Permission rules
----------------
Public route:
  - Any authenticated user.
  - Unauthenticated visitors → 302 to login.

Workspace route:
  - Workspace member (viewer+) only.
  - Non-members → 404 (fail-closed, via ``get_workspace_for_user``).

Cache policy
------------
Workspace responses carry ``Cache-Control: private, no-store``.
Public responses do not set an explicit cache header (inherits global default).
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from backend.models.post import PostStatus
from backend.services import workspace_service as ws_svc
from backend.services.fork_recommendation_service import recommend
from backend.services.prompt_service import get_prompt_by_slug
from backend.utils.auth import get_current_user

fork_rec_bp = Blueprint("fork_rec", __name__)


# ── Public route ───────────────────────────────────────────────────────────────


@fork_rec_bp.get("/prompts/<slug>/recommendations")
def public_recommendations(slug: str):
    """Ranked fork recommendations for a public prompt."""
    user = get_current_user()
    if user is None:
        return redirect(url_for("auth.login", next=request.path))

    prompt = get_prompt_by_slug(slug, workspace_id=None)
    if prompt is None or prompt.status != PostStatus.published:
        abort(404)

    recommendations = recommend(user, prompt, workspace=None)

    return render_template(
        "prompts/recommendations.html",
        prompt=prompt,
        recommendations=recommendations,
        workspace=None,
        current_user=user,
    )


# ── Workspace route ────────────────────────────────────────────────────────────


def _no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers.pop("Expires", None)
    response.headers.pop("Pragma", None)
    return response


@fork_rec_bp.get("/w/<ws_slug>/prompts/<slug>/recommendations")
def ws_recommendations(ws_slug: str, slug: str):
    """Ranked fork recommendations for a workspace prompt (member+)."""
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(ws_slug, user)

    prompt = get_prompt_by_slug(slug, workspace_id=ws.id)
    if prompt is None:
        abort(404)

    recommendations = recommend(user, prompt, workspace=ws)

    resp = make_response(
        render_template(
            "prompts/recommendations.html",
            prompt=prompt,
            recommendations=recommendations,
            workspace=ws,
            current_user=user,
        )
    )
    return _no_store(resp)
