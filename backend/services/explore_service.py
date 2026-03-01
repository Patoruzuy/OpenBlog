"""Explore service — aggregates public content for the /explore discovery page.

The explore page surfaces three feeds under separate tabs:
  - Posts:     recently published articles, sorted newest-first.
  - Topics:    all tags ordered by published-post count, descending.
  - Revisions: two sections — open (pending) queue + recently accepted.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.tag import PostTag, Tag

_POSTS_PER_PAGE = 20
_TAGS_LIMIT = 60
_REVISIONS_PER_SECTION = 20


class ExploreService:
    # ── Posts tab ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_posts(page: int = 1) -> tuple[list[Post], int]:
        """Return paginated published posts, newest first."""
        from sqlalchemy.orm import joinedload  # noqa: PLC0415

        # INV-001: public published posts only.
        q = (
            select(Post)
            .where(
                Post.workspace_id.is_(None),
                Post.status == PostStatus.published,
                Post.published_at.is_not(None),
            )
            .options(
                joinedload(Post.author),
                joinedload(Post.tags),
            )
            .order_by(Post.published_at.desc())
        )
        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        offset = (page - 1) * _POSTS_PER_PAGE
        posts = list(
            db.session.execute(q.offset(offset).limit(_POSTS_PER_PAGE))
            .unique()
            .scalars()
        )
        return posts, total

    # ── Topics tab ────────────────────────────────────────────────────────────

    @staticmethod
    def get_topics() -> list[dict]:
        """Return all tags annotated with their published-post counts."""
        post_count_col = func.count(Post.id).label("post_count")
        rows = db.session.execute(
            select(Tag, post_count_col)
            .outerjoin(PostTag, PostTag.c.tag_id == Tag.id)
            .outerjoin(
                Post,
                (Post.id == PostTag.c.post_id)
                & (Post.status == PostStatus.published)
                & Post.workspace_id.is_(None),
            )
            .group_by(Tag.id)
            .order_by(post_count_col.desc(), Tag.name)
            .limit(_TAGS_LIMIT)
        ).all()
        return [{"tag": row.Tag, "post_count": row.post_count} for row in rows]

    # ── Revisions tab ─────────────────────────────────────────────────────────

    @staticmethod
    def get_open_revisions(
        page: int = 1,
    ) -> tuple[list[Revision], int]:
        """Return pending revisions, oldest-first (review queue order)."""
        q = (
            select(Revision)
            .where(Revision.status == RevisionStatus.pending)
            .options(
                joinedload(Revision.post),
                joinedload(Revision.author),
            )
            .order_by(Revision.created_at.asc())
        )
        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        offset = (page - 1) * _REVISIONS_PER_SECTION
        revisions = list(
            db.session.execute(q.offset(offset).limit(_REVISIONS_PER_SECTION))
            .unique()
            .scalars()
        )
        return revisions, total

    @staticmethod
    def get_accepted_revisions(
        page: int = 1,
    ) -> tuple[list[Revision], int]:
        """Return recently accepted revisions, newest-accepted first."""
        q = (
            select(Revision)
            .where(Revision.status == RevisionStatus.accepted)
            .options(
                joinedload(Revision.post),
                joinedload(Revision.author),
            )
            .order_by(Revision.updated_at.desc())
        )
        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        offset = (page - 1) * _REVISIONS_PER_SECTION
        revisions = list(
            db.session.execute(q.offset(offset).limit(_REVISIONS_PER_SECTION))
            .unique()
            .scalars()
        )
        return revisions, total
