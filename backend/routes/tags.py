"""SSR — Tags index page.

Routes
------
GET /tags/    list all tags with their published post counts
"""

from __future__ import annotations

from flask import Blueprint, render_template
from flask_babel import lazy_gettext as _l
from sqlalchemy import func, select

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.tag import PostTag, Tag

ssr_tags_bp = Blueprint("tags", __name__, url_prefix="/tags")

# Curated descriptions for well-known tags.
# Merging at the route level avoids a DB schema change.
_TAG_DESCRIPTIONS: dict[str, str] = {
    "flask": _l("Routing, blueprints, deployment, and real-world patterns."),
    "python": _l("Language features, tooling, packaging, and ecosystem notes."),
    "postgres": _l("Queries, indexing, full-text search, and migrations."),
    "postgresql": _l("Queries, indexing, full-text search, and migrations."),
    "celery": _l("Task queues, beat scheduling, and worker configuration."),
    "redis": _l("Caching, queuing, pub/sub, and persistence patterns."),
    "docker": _l("Containerisation, Compose, and production Dockerfiles."),
    "linux": _l("Shell scripting, system administration, and tooling."),
    "git": _l("Branching, rebasing, workflows, and repo management."),
    "sql": _l("Queries, schema design, indexing, and optimisation."),
    "api": _l("REST design, versioning, auth, and documentation."),
    "testing": _l("Unit tests, integration tests, fixtures, and coverage."),
    "devops": _l("CI/CD pipelines, infrastructure, and deployment automation."),
    "security": _l("Auth patterns, input validation, secrets management."),
    "ai": _l("Language models, embeddings, retrieval, and practical ML."),
    "javascript": _l("Browser JS, tooling, and small scripting patterns."),
    "typescript": _l("Type safety, configuration, and real-world usage."),
    "architecture": _l("System design, service boundaries, and trade-offs."),
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
