"""SSR blueprint — user profile page.

Routes
------
GET /users/<username>   public profile page
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template

from backend.services.badge_service import BadgeService
from backend.services.pinned_post_service import PinnedPostService
from backend.services.privacy_service import PrivacyService
from backend.services.user_analytics_service import (
    build_contribution_heatmap,
    build_ontology_contributions,
    build_user_contribution_summary,
    compute_contribution_streak,
)
from backend.services.user_service import UserService
from backend.utils.auth import get_current_user

ssr_users_bp = Blueprint("users", __name__, url_prefix="/users")


@ssr_users_bp.get("/<username>")
def profile(username: str):
    """Render the public profile page for *username*."""
    user = UserService.get_by_username(username)
    if user is None or not user.is_active:
        abort(404)

    viewer = get_current_user()
    viewer_id: int | None = viewer.id if viewer is not None else None
    viewer_is_self: bool = viewer_id is not None and viewer_id == user.id

    # Apply privacy filter before rendering anything
    privacy_view = PrivacyService.get_public_view(user, viewer)

    # Paginate published posts (page 1 preview — full list via API)
    page = 1
    per_page = 10
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from backend.extensions import db
    from backend.models.post import Post, PostStatus

    base = (
        sa_select(Post)
        .where(Post.author_id == user.id, Post.status == PostStatus.published)
        .order_by(Post.published_at.desc())
    )
    total_posts = (
        db.session.scalar(sa_select(func.count()).select_from(base.subquery())) or 0
    )
    posts = list(db.session.scalars(base.offset(0).limit(per_page)))
    total_pages = (total_posts + per_page - 1) // per_page if total_posts else 0

    is_following = (
        UserService.is_following(viewer_id, user.id)
        if viewer_id is not None and viewer_id != user.id
        else None
    )

    # Pinned posts (always shown when profile is visible)
    pinned_posts = (
        PinnedPostService.get_pinned(user.id) if privacy_view.get("visible") else []
    )

    # Contribution analytics — heatmap, summary, ontology, streak.
    # Owner (viewer_is_self) sees all contributions; public view sees only
    # public (workspace_id IS NULL) contributions.
    _show_contrib = privacy_view.get("show_contributions", False)
    _public_only = not viewer_is_self

    if _show_contrib:
        contrib_data = build_contribution_heatmap(user.id, public_only=_public_only)
        contrib_summary = build_user_contribution_summary(
            user.id, public_only=_public_only
        )
        contrib_ontology = build_ontology_contributions(
            user.id, public_only=_public_only
        )
        contrib_streak = compute_contribution_streak(user.id, public_only=_public_only)
    else:
        contrib_data = {"weeks": [], "total": 0}
        contrib_summary = {
            "posts_published": 0,
            "revisions_submitted": 0,
            "revisions_accepted": 0,
            "ai_reviews_requested": 0,
            "benchmarks_run": 0,
            "ab_experiments_created": 0,
        }
        contrib_ontology = []
        contrib_streak = {"current_streak": 0, "longest_streak": 0}

    # Badges — public visitors see only public-scoped badges
    user_badges = BadgeService.list_for_user(user.id, public_only=not viewer_is_self)

    # Recent activity — last 10 published posts (simple activity feed)
    from backend.models.comment import Comment

    recent_posts_q = list(
        db.session.scalars(
            sa_select(Post)
            .where(Post.author_id == user.id, Post.status == PostStatus.published)
            .order_by(Post.published_at.desc())
            .limit(5)
        )
    )
    recent_comments_q = list(
        db.session.scalars(
            sa_select(Comment)
            .where(Comment.author_id == user.id)
            .order_by(Comment.created_at.desc())
            .limit(5)
        )
    )
    # Merge and sort by date, take 10
    activity: list[dict] = []
    for p in recent_posts_q:
        activity.append({"type": "post", "obj": p, "at": p.published_at})
    for c in recent_comments_q:
        activity.append({"type": "comment", "obj": c, "at": c.created_at})
    activity.sort(
        key=lambda x: x["at"] or __import__("datetime").datetime.min, reverse=True
    )
    recent_activity = activity[:10]

    return render_template(
        "users/profile.html",
        profile_user=user,
        privacy_view=privacy_view,
        posts=posts if privacy_view.get("show_contributions") else [],
        total_posts=total_posts,
        page=page,
        total_pages=total_pages,
        follower_count=UserService.follower_count(user.id),
        following_count=UserService.following_count(user.id),
        is_following=is_following,
        viewer=viewer,
        viewer_is_self=viewer_is_self,
        pinned_posts=pinned_posts,
        contrib_data=contrib_data,
        contrib_summary=contrib_summary,
        contrib_ontology=contrib_ontology,
        contrib_streak=contrib_streak,
        user_badges=user_badges,
        recent_activity=recent_activity,
    )
