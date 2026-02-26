"""SSR — Tags index page.

Routes
------
GET /tags/    list all tags with their published post counts
"""

from __future__ import annotations

from flask import Blueprint, render_template
from sqlalchemy import func, select

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.tag import PostTag, Tag

ssr_tags_bp = Blueprint("tags", __name__, url_prefix="/tags")


@ssr_tags_bp.get("/")
def tag_index():
    """Render all tags with their published-post counts, sorted by popularity."""
    # Left-join Tags → PostTag → Post(published only) so tags with 0 posts
    # still appear in the list.
    post_count = func.count(Post.id).label("post_count")
    stmt = (
        select(Tag, post_count)
        .outerjoin(PostTag, PostTag.c.tag_id == Tag.id)
        .outerjoin(
            Post,
            (Post.id == PostTag.c.post_id) & (Post.status == PostStatus.published),
        )
        .group_by(Tag.id)
        .order_by(post_count.desc(), Tag.name)
    )
    rows = db.session.execute(stmt).all()
    tags = [{"tag": row.Tag, "post_count": row.post_count} for row in rows]

    return render_template("tags/index.html", tags=tags)
