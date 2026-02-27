"""CommentAttachment model — file references attached to comments.

Storage is deferred (no file is persisted in this pass).  The endpoint
validates the upload and records the intended filename; actual storage,
serving, and Content-Disposition headers are implemented in a later pass
outside of static/.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class CommentAttachment(db.Model):
    """Metadata record for a file the user intends to attach to a comment."""

    __tablename__ = "comment_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    comment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,  # NULL while the comment hasn't been submitted yet
        index=True,
    )
    uploader_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Original filename as supplied by the client (sanitised at the service layer)
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    # MIME type as determined by server-side magic-byte inspection
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False, default="application/octet-stream")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 'pending' | 'stored' | 'failed'
    storage_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    comment: Mapped[Comment | None] = relationship("Comment")  # type: ignore[name-defined]  # noqa: F821
    uploader: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_comment_attachments_comment", "comment_id"),
    )
