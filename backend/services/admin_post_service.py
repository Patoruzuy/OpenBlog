"""Admin post service — privileged post management operations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision
from backend.models.tag import Tag
from backend.models.user import User

_PAGE_SIZE = 30


class AdminPostService:
    @staticmethod
    def list_posts(
        *,
        status: str | None = None,
        author_id: int | None = None,
        tag_slug: str | None = None,
        featured: bool | None = None,
        q: str | None = None,
        page: int = 1,
        sort: str = "updated_desc",
    ) -> tuple[list[Post], int]:
        """Paginated, filterable post list for the admin."""
        query = (
            select(Post)
            .options(joinedload(Post.author), joinedload(Post.tags))
        )

        if status:
            try:
                query = query.where(Post.status == PostStatus(status))
            except ValueError:
                pass
        if author_id:
            query = query.where(Post.author_id == author_id)
        if featured is not None:
            query = query.where(Post.is_featured == featured)
        if tag_slug:
            query = query.join(Post.tags).where(Tag.slug == tag_slug)
        if q:
            like = f"%{q.lower()}%"
            query = query.where(
                or_(Post.title.ilike(like), Post.slug.ilike(like))
            )

        _SORT = {
            "updated_desc": Post.updated_at.desc(),
            "updated_asc":  Post.updated_at.asc(),
            "created_desc": Post.created_at.desc(),
            "views_desc":   Post.view_count.desc(),
            "title_asc":    Post.title.asc(),
        }
        query = query.order_by(_SORT.get(sort, Post.updated_at.desc()))

        total = db.session.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0
        offset = (page - 1) * _PAGE_SIZE
        items = list(
            db.session.scalars(query.offset(offset).limit(_PAGE_SIZE)).unique().all()
        )
        return items, total

    @staticmethod
    def set_status(post: Post, new_status: PostStatus, actor: User) -> None:
        """Transition *post* to *new_status* (publish / unpublish / archive)."""
        post.status = new_status
        if new_status == PostStatus.published and post.publish_at is None:
            post.publish_at = datetime.now(UTC)
        elif new_status in (PostStatus.draft, PostStatus.archived):
            post.publish_at = None
        db.session.commit()

    @staticmethod
    def toggle_featured(post: Post) -> None:
        post.is_featured = not post.is_featured
        db.session.commit()

    @staticmethod
    def delete_post(post: Post) -> None:
        db.session.delete(post)
        db.session.commit()

    @staticmethod
    def get_with_revisions(post_id: int) -> Post | None:
        return db.session.scalar(
            select(Post)
            .where(Post.id == post_id)
            .options(
                joinedload(Post.author),
                joinedload(Post.tags),
                joinedload(Post.revisions).joinedload(Revision.author),
            )
        )
