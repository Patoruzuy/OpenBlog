"""Moderation service — revision review queue and comment moderation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.comment import Comment
from backend.models.post import Post
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User
from backend.services.revision_service import RevisionService

_PAGE_SIZE = 30


class ModerationError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ModerationService:
    # ── Revision queue ────────────────────────────────────────────────────────

    @staticmethod
    def list_revisions(
        *,
        status: str | None = "pending",
        post_id: int | None = None,
        author_id: int | None = None,
        q: str | None = None,
        page: int = 1,
    ) -> tuple[list[Revision], int]:
        query = (
            select(Revision)
            .options(
                joinedload(Revision.author),
                joinedload(Revision.post),
                joinedload(Revision.reviewed_by),
            )
            .order_by(desc(Revision.created_at))
        )
        if status and status != "all":
            try:
                query = query.where(Revision.status == RevisionStatus(status))
            except ValueError:
                pass
        if post_id:
            query = query.where(Revision.post_id == post_id)
        if author_id:
            query = query.where(Revision.author_id == author_id)
        if q:
            like = f"%{q.lower()}%"
            query = query.join(Revision.post).where(
                or_(Post.title.ilike(like), Revision.summary.ilike(like))
            )

        total = db.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        offset = (page - 1) * _PAGE_SIZE
        items = list(db.session.scalars(query.offset(offset).limit(_PAGE_SIZE)).unique().all())
        return items, total

    @staticmethod
    def accept_revision(revision_id: int, reviewer: User, note: str | None = None) -> Revision:
        """Accept a pending revision via RestRevisionService."""
        rev = db.session.get(Revision, revision_id)
        if rev is None:
            raise ModerationError("Revision not found.", 404)
        if rev.status != RevisionStatus.pending:
            raise ModerationError("Revision is not pending.", 400)
        # Delegate to the existing service to keep business logic centralised.
        RevisionService.accept(revision_id=revision_id, reviewer_id=reviewer.id)
        # Persist reviewer note if provided
        if note:
            db.session.refresh(rev)
            rev.rejection_note = note  # reuse field for reviewer note
            db.session.commit()
        return rev

    @staticmethod
    def reject_revision(revision_id: int, reviewer: User, note: str | None) -> Revision:
        """Reject a pending revision."""
        rev = db.session.get(Revision, revision_id)
        if rev is None:
            raise ModerationError("Revision not found.", 404)
        if rev.status != RevisionStatus.pending:
            raise ModerationError("Revision is not pending.", 400)
        RevisionService.reject(
            revision_id=revision_id,
            reviewer_id=reviewer.id,
            note=note or "",
        )
        return rev

    # ── Comment moderation ────────────────────────────────────────────────────

    @staticmethod
    def list_comments(
        *,
        flagged_only: bool = False,
        deleted: bool | None = None,
        q: str | None = None,
        page: int = 1,
    ) -> tuple[list[Comment], int]:
        query = (
            select(Comment)
            .options(joinedload(Comment.author), joinedload(Comment.post))
            .order_by(desc(Comment.created_at))
        )
        if flagged_only:
            query = query.where(Comment.is_flagged == True)  # noqa: E712
        if deleted is not None:
            query = query.where(Comment.is_deleted == deleted)
        if q:
            query = query.where(Comment.body.ilike(f"%{q}%"))

        total = db.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        offset = (page - 1) * _PAGE_SIZE
        items = list(db.session.scalars(query.offset(offset).limit(_PAGE_SIZE)).unique().all())
        return items, total

    @staticmethod
    def hide_comment(comment_id: int) -> None:
        c = db.session.get(Comment, comment_id)
        if c is None:
            raise ModerationError("Comment not found.", 404)
        c.is_deleted = True
        c.body = "[removed by moderator]"
        db.session.commit()

    @staticmethod
    def unflag_comment(comment_id: int) -> None:
        c = db.session.get(Comment, comment_id)
        if c is None:
            raise ModerationError("Comment not found.", 404)
        c.is_flagged = False
        db.session.commit()
