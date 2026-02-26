"""SSR blueprint — user profile page.

Routes
------
GET /users/<username>   public profile page
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template

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
    total_posts = db.session.scalar(sa_select(func.count()).select_from(base.subquery())) or 0
    posts = list(db.session.scalars(base.offset(0).limit(per_page)))
    total_pages = (total_posts + per_page - 1) // per_page if total_posts else 0

    is_following = (
        UserService.is_following(viewer_id, user.id)
        if viewer_id is not None and viewer_id != user.id
        else None
    )

    return render_template(
        "users/profile.html",
        profile_user=user,
        posts=posts,
        total_posts=total_posts,
        page=page,
        total_pages=total_pages,
        follower_count=UserService.follower_count(user.id),
        following_count=UserService.following_count(user.id),
        is_following=is_following,
        viewer=viewer,
    )
