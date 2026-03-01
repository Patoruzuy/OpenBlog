"""Service helpers for PostVersion snapshots."""

from __future__ import annotations

from sqlalchemy import select

from backend.extensions import db
from backend.models.post import Post
from backend.models.post_version import PostVersion


class PostVersionService:
    @staticmethod
    def get_markdown_for_version(post_id: int, version_number: int) -> str | None:
        """Return the markdown body stored for *version_number* of *post_id*.

        Falls back to ``Post.markdown_body`` when the version number equals the
        post's current version *and* no dedicated snapshot exists yet (useful for
        the latest version before the second revision is accepted).

        Returns ``None`` when the version cannot be resolved.
        """
        row = db.session.scalar(
            select(PostVersion).where(
                PostVersion.post_id == post_id,
                PostVersion.version_number == version_number,
            )
        )
        if row is not None:
            return row.markdown_body

        # Fallback: if they asked for the current version use live content
        post = db.session.get(Post, post_id)
        if post is not None and post.version == version_number:
            return post.markdown_body

        return None

    @staticmethod
    def get_available_versions(post_id: int) -> list[int]:
        """Return sorted list of version numbers that have snapshots stored."""
        return list(
            db.session.scalars(
                select(PostVersion.version_number)
                .where(PostVersion.post_id == post_id)
                .order_by(PostVersion.version_number)
            )
        )
