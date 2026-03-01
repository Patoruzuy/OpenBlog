"""Admin topics/tags service."""

from __future__ import annotations

import re

from sqlalchemy import desc, func, or_, select

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.tag import PostTag, Tag

_PAGE_SIZE = 50


class AdminTagError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "tag"


class AdminTagService:
    @staticmethod
    def list_tags(*, q: str | None = None, page: int = 1) -> tuple[list[dict], int]:
        """Return tags enriched with their published post counts."""
        # Subquery: count of published posts per tag_id
        post_count_sq = (
            select(PostTag.c.tag_id, func.count(Post.id).label("post_count"))
            .join(Post, Post.id == PostTag.c.post_id)
            .where(Post.status == PostStatus.published)
            .group_by(PostTag.c.tag_id)
            .subquery()
        )
        base_q = select(Tag)
        if q:
            base_q = base_q.where(
                or_(Tag.name.ilike(f"%{q}%"), Tag.slug.ilike(f"%{q}%"))
            )
        total = (
            db.session.scalar(select(func.count()).select_from(base_q.subquery())) or 0
        )

        query = (
            select(
                Tag, func.coalesce(post_count_sq.c.post_count, 0).label("post_count")
            )
            .outerjoin(post_count_sq, post_count_sq.c.tag_id == Tag.id)
            .order_by(desc("post_count"), Tag.name)
        )
        if q:
            query = query.where(or_(Tag.name.ilike(f"%{q}%"), Tag.slug.ilike(f"%{q}%")))

        offset = (page - 1) * _PAGE_SIZE
        rows = db.session.execute(query.offset(offset).limit(_PAGE_SIZE)).all()
        items = [{"tag": row.Tag, "post_count": row.post_count} for row in rows]
        return items, total

    @staticmethod
    def create(
        *, name: str, description: str | None = None, color: str | None = None
    ) -> Tag:
        name = name.strip()
        if not name:
            raise AdminTagError("Tag name is required.")
        slug = _slugify(name)
        existing = db.session.scalar(select(Tag).where(Tag.slug == slug))
        if existing:
            raise AdminTagError(f"Tag with slug '{slug}' already exists.", 409)
        tag = Tag(
            name=name, slug=slug, description=description or None, color=color or None
        )
        db.session.add(tag)
        db.session.commit()
        return tag

    @staticmethod
    def update(
        tag: Tag,
        *,
        name: str | None = None,
        description: str | None = None,
        color: str | None = None,
    ) -> Tag:
        if name is not None:
            name = name.strip()
            if not name:
                raise AdminTagError("Tag name cannot be empty.")
            tag.name = name
        if description is not None:
            tag.description = description or None
        if color is not None:
            tag.color = color or None
        db.session.commit()
        return tag

    @staticmethod
    def delete(tag: Tag) -> None:
        db.session.delete(tag)
        db.session.commit()

    @staticmethod
    def get_by_slug(slug: str) -> Tag | None:
        return db.session.scalar(select(Tag).where(Tag.slug == slug))
