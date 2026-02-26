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

# Curated descriptions for well-known tags.
# Merging at the route level avoids a DB schema change.
_TAG_DESCRIPTIONS: dict[str, str] = {
    "flask": "Routing, blueprints, deployment, and real-world patterns.",
    "python": "Language features, tooling, packaging, and ecosystem notes.",
    "postgres": "Queries, indexing, full-text search, and migrations.",
    "postgresql": "Queries, indexing, full-text search, and migrations.",
    "celery": "Task queues, beat scheduling, and worker configuration.",
    "redis": "Caching, queuing, pub/sub, and persistence patterns.",
    "docker": "Containerisation, Compose, and production Dockerfiles.",
    "linux": "Shell scripting, system administration, and tooling.",
    "git": "Branching, rebasing, workflows, and repo management.",
    "sql": "Queries, schema design, indexing, and optimisation.",
    "api": "REST design, versioning, auth, and documentation.",
    "testing": "Unit tests, integration tests, fixtures, and coverage.",
    "devops": "CI/CD pipelines, infrastructure, and deployment automation.",
    "security": "Auth patterns, input validation, secrets management.",
    "ai": "Language models, embeddings, retrieval, and practical ML.",
    "javascript": "Browser JS, tooling, and small scripting patterns.",
    "typescript": "Type safety, configuration, and real-world usage.",
    "architecture": "System design, service boundaries, and trade-offs.",
}


@ssr_tags_bp.get("/")
def tag_index():
    """Render all tags with their published-post counts, sorted by popularity."""
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
    tags = [
        {
            "tag": row.Tag,
            "post_count": row.post_count,
            "description": (
                row.Tag.description
                or _TAG_DESCRIPTIONS.get(row.Tag.slug.lower())
                or None
            ),
        }
        for row in rows
    ]

    return render_template("tags/index.html", tags=tags)
