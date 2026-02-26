"""Index route — renders the home page."""

from __future__ import annotations

from flask import Blueprint, render_template
from sqlalchemy import func, select

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.tag import PostTag, Tag
from backend.models.user import User
from backend.routes.tags import _TAG_DESCRIPTIONS
from backend.services.post_service import PostService
from backend.services.revision_service import RevisionService

index_bp = Blueprint("index", __name__)


@index_bp.get("/")
def index():
    # Featured / recent posts
    featured_post = PostService.get_featured()
    recent_posts, _ = PostService.list_published(1, 6)
    # Remove featured from recents if it appears there
    if featured_post:
        recent_posts = [p for p in recent_posts if p.id != featured_post.id][:6]

    # Recently revised posts (version > 1)
    updated_posts = PostService.list_recently_updated(limit=4)

    # Open revision queue (limit to 5 for homepage widget)
    open_revisions, open_revision_count = RevisionService.list_pending(page=1, per_page=5)

    # Top tags by published post count (limit 10)
    post_count_col = func.count(Post.id).label("post_count")
    top_tags = [
        {
            "tag": row.Tag,
            "post_count": row.post_count,
            "description": _TAG_DESCRIPTIONS.get(row.Tag.slug),
        }
        for row in db.session.execute(
            select(Tag, post_count_col)
            .outerjoin(PostTag, PostTag.c.tag_id == Tag.id)
            .outerjoin(
                Post,
                (Post.id == PostTag.c.post_id)
                & (Post.status == PostStatus.published),
            )
            .group_by(Tag.id)
            .order_by(post_count_col.desc(), Tag.name)
            .limit(10)
        ).all()
    ]

    # Platform stats for hero (zero-safe)
    total_posts: int = db.session.scalar(
        select(func.count(Post.id)).where(Post.status == PostStatus.published)
    ) or 0
    accepted_revisions: int = db.session.scalar(
        select(func.count(Revision.id)).where(
            Revision.status == RevisionStatus.accepted
        )
    ) or 0
    contributor_count: int = db.session.scalar(
        select(func.count(func.distinct(Revision.author_id))).where(
            Revision.status == RevisionStatus.accepted
        )
    ) or 0

    return render_template(
        "index.html",
        title="OpenBlog",
        featured_post=featured_post,
        recent_posts=recent_posts,
        updated_posts=updated_posts,
        open_revisions=open_revisions,
        open_revision_count=open_revision_count,
        top_tags=top_tags,
        stats={
            "posts": total_posts,
            "revisions_accepted": accepted_revisions,
            "contributors": contributor_count,
        },
    )
