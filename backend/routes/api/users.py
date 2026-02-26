"""JSON API — user profile and follow endpoints.

Routes
------
GET    /api/users/<username>             public profile + stats
PATCH  /api/users/<username>             update own profile  [authenticated]
POST   /api/users/<username>/follow      follow a user       [authenticated]
DELETE /api/users/<username>/follow      unfollow a user     [authenticated]
GET    /api/users/<username>/followers   paginated follower list
GET    /api/users/<username>/following   paginated following list
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.extensions import csrf
from backend.models.post import PostStatus
from backend.models.user import User
from backend.services.user_service import UserService, UserServiceError
from backend.utils.auth import api_require_auth, get_current_user
from backend.schemas import UpdateProfileSchema, load_json

api_users_bp = Blueprint("api_users", __name__, url_prefix="/api/users")
csrf.exempt(api_users_bp)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _user_dict(user: User, *, viewer_id: int | None = None) -> dict:
    """Serialise a public user profile to a plain dict.

    Parameters
    ----------
    viewer_id:
        When supplied the response includes ``is_following`` to indicate
        whether the viewer already follows this profile.
    """
    d: dict = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "website_url": user.website_url,
        "github_url": user.github_url,
        "tech_stack": user.tech_stack,
        "location": user.location,
        "role": user.role.value,
        "reputation_score": user.reputation_score,
        "member_since": user.created_at.isoformat(),
        "post_count": UserService.published_post_count(user.id),
        "follower_count": UserService.follower_count(user.id),
        "following_count": UserService.following_count(user.id),
    }
    if viewer_id is not None:
        d["is_following"] = UserService.is_following(viewer_id, user.id)
    return d


def _user_stub(user: User) -> dict:
    """Minimal user dict for follower/following list items."""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "bio": user.bio,
    }


def _get_user_or_404(username: str):
    user = UserService.get_by_username(username)
    if user is None or not user.is_active:
        return None, (jsonify({"error": "User not found."}), 404)
    return user, None


# ── GET /api/users/<username> ─────────────────────────────────────────────────


@api_users_bp.get("/<username>")
def get_profile(username: str):
    """Return the public profile of a user."""
    user, err = _get_user_or_404(username)
    if err:
        return err

    viewer = get_current_user()
    # Pass viewer_id only when the viewer is a different user — avoids a
    # self-referential "is_following" field appearing on own-profile requests.
    viewer_id = (
        viewer.id if (viewer is not None and viewer.id != user.id) else None
    )
    return jsonify(_user_dict(user, viewer_id=viewer_id))


# ── PATCH /api/users/<username> ───────────────────────────────────────────────


@api_users_bp.patch("/<username>")
@api_require_auth
def update_profile(username: str):
    """Update the authenticated user's own profile fields."""
    user, err = _get_user_or_404(username)
    if err:
        return err

    current = get_current_user()
    if current.id != user.id:
        return jsonify({"error": "You may only edit your own profile."}), 403

    raw = request.get_json(silent=True) or {}
    data, err = load_json(UpdateProfileSchema(), raw)
    if err:
        return err
    # Only forward keys the caller actually sent; absent fields must not
    # overwrite existing values (partial update semantics).
    _profile_fields = {
        "display_name", "bio", "avatar_url",
        "website_url", "github_url", "tech_stack", "location",
    }
    kwargs = {k: data[k] for k in _profile_fields if k in raw}
    try:
        updated = UserService.update_profile(user, **kwargs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_user_dict(updated, viewer_id=current.id))


# ── POST /api/users/<username>/follow ─────────────────────────────────────────


@api_users_bp.post("/<username>/follow")
@api_require_auth
def follow_user(username: str):
    """Follow a user."""
    user, err = _get_user_or_404(username)
    if err:
        return err

    current = get_current_user()
    try:
        UserService.follow(current.id, user.id)
    except UserServiceError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "following": True,
            "follower_count": UserService.follower_count(user.id),
        }
    )


# ── DELETE /api/users/<username>/follow ───────────────────────────────────────


@api_users_bp.delete("/<username>/follow")
@api_require_auth
def unfollow_user(username: str):
    """Unfollow a user."""
    user, err = _get_user_or_404(username)
    if err:
        return err

    current = get_current_user()
    try:
        UserService.unfollow(current.id, user.id)
    except UserServiceError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "following": False,
            "follower_count": UserService.follower_count(user.id),
        }
    )


# ── GET /api/users/<username>/followers ───────────────────────────────────────


@api_users_bp.get("/<username>/followers")
def list_followers(username: str):
    """Return a paginated list of users who follow *username*."""
    user, err = _get_user_or_404(username)
    if err:
        return err

    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    users, total = UserService.get_followers(user.id, page, per_page)
    return jsonify(
        {
            "users": [_user_stub(u) for u in users],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if total else 0,
        }
    )


# ── GET /api/users/<username>/following ───────────────────────────────────────


@api_users_bp.get("/<username>/following")
def list_following(username: str):
    """Return a paginated list of users that *username* is following."""
    user, err = _get_user_or_404(username)
    if err:
        return err

    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    users, total = UserService.get_following(user.id, page, per_page)
    return jsonify(
        {
            "users": [_user_stub(u) for u in users],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if total else 0,
        }
    )


# ── GET /api/users/<username>/posts ───────────────────────────────────────────


@api_users_bp.get("/<username>/posts")
def list_user_posts(username: str):
    """Return a paginated list of published posts by *username*."""
    user, err = _get_user_or_404(username)
    if err:
        return err

    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    # Reuse PostService.list_published with author filter via tag_slug=None,
    # then manually filter. For a production query we go direct.
    from sqlalchemy import select as sa_select  # local to avoid circular import

    from backend.extensions import db as _db
    from backend.models.post import Post

    base = (
        sa_select(Post)
        .where(Post.author_id == user.id, Post.status == PostStatus.published)
        .order_by(Post.published_at.desc())
    )
    from sqlalchemy import func

    total = _db.session.scalar(sa_select(func.count()).select_from(base.subquery())) or 0
    posts = list(
        _db.session.scalars(base.offset((page - 1) * per_page).limit(per_page))
    )

    # Inline minimal post dict to avoid circular import with posts blueprint.
    return jsonify(
        {
            "posts": [
                {
                    "id": p.id,
                    "slug": p.slug,
                    "title": p.title,
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                    "reading_time_minutes": p.reading_time_minutes,
                    "tags": [{"slug": t.slug, "name": t.name} for t in p.tags],
                }
                for p in posts
            ],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if total else 0,
        }
    )
