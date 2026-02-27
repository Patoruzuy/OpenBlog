"""CommentAttachment model — file references attached to comments.

Files are stored outside static/ under MEDIA_ROOT/comment_attachments/<id>/<uuid>.<ext>.
The stored_path column records the path relative to MEDIA_ROOT.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class CommentAttachment(db.Model):
    """Metadata record for a file attached to a comment."""

    __tablename__ = "comment_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    comment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,  # NULL while the comment is being created
        index=True,
    )
    uploader_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Client-supplied metadata (display only; never used as FS path) ─────
    original_filename: Mapped[str] = mapped_column(String(260), nullable=False)
    mime_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="application/octet-stream"
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Server-assigned storage ────────────────────────────────────────────
    # Path relative to MEDIA_ROOT, e.g. "comment_attachments/42/a3b4c5.png"
    stored_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # SHA-256 hex digest for integrity checks (populated after write)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # True iff the file is a safe image type (png/jpg/webp/gif)
    is_image: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Status ─────────────────────────────────────────────────────────────
    # "pending" → not yet stored, "stored" → on disk, "deleted" → soft-delete
    storage_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    comment: Mapped[Comment | None] = relationship(
        "Comment", back_populates="attachments", overlaps="attachments"
    )  # type: ignore[name-defined]  # noqa: F821
    uploader: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = ()
