"""SSR — Explore discovery page.

Routes
------
GET /explore          redirect to /explore?tab=posts
GET /explore?tab=posts|topics|revisions
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from backend.services.explore_service import ExploreService

explore_bp = Blueprint("explore", __name__, url_prefix="/explore")

_VALID_TABS = frozenset({"posts", "topics", "revisions"})
_DEFAULT_TAB = "posts"


@explore_bp.get("/")
def explore_index():
    tab = request.args.get("tab", _DEFAULT_TAB).lower()
    if tab not in _VALID_TABS:
        return redirect(url_for("explore.explore_index", tab=_DEFAULT_TAB))

    page = max(1, request.args.get("page", 1, type=int))

    ctx: dict = {"tab": tab, "page": page}

    if tab == "posts":
        posts, total = ExploreService.get_posts(page=page)
        ctx["posts"] = posts
        ctx["total_posts"] = total
        ctx["total_pages"] = max(1, (total + 19) // 20)

    elif tab == "topics":
        ctx["topics"] = ExploreService.get_topics()

    else:  # revisions
        open_revisions, open_total = ExploreService.get_open_revisions(page=page)
        accepted_revisions, accepted_total = ExploreService.get_accepted_revisions(
            page=page
        )
        ctx["open_revisions"] = open_revisions
        ctx["open_total"] = open_total
        ctx["accepted_revisions"] = accepted_revisions
        ctx["accepted_total"] = accepted_total

    return render_template("explore/index.html", **ctx)
