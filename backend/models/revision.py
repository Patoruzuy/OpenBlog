"""Revision model — GitHub-style edit proposals for posts.

Workflow
--------
1. A contributor calls POST /api/posts/{slug}/revisions with their proposed
   markdown.
2. A ``Revision`` record is created with status=pending.
3. An admin or editor reviews the diff (computed on demand from
   base_version.markdown_body → proposed_markdown via difflib).
4. On accept: a new PostVersion is created, Post.version is bumped, the
   revision status is set to accepted, and a reputation event is emitted.
5. On reject: status is set to rejected with an optional rejection note.

Design notes
------------
- ``proposed_markdown`` is the full canonical text proposed by the contributor
  (not just a patch/diff).  The diff is *derived* and can be recomputed at any
  time from base_version_id → proposed_markdown.
- Storing the full proposed content avoids dealing with patch conflicts at write
  time and keeps the proposal self-contained.
- ``diff_cache`` stores the pre-computed unified diff for fast UI rendering; it
  is nullable and treated as a derived cache (safe to invalidate).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class RevisionStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class Revision(db.Model):
    """A proposed change to a post submitted by a contributor."""

    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Post reference ─────────────────────────────────────────────────────
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Authorship ─────────────────────────────────────────────────────────
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Version anchor ─────────────────────────────────────────────────────
    # The post version this proposal is based on.  Used to detect staleness
    # (if post.version > base_version_number when reviewing, the diff context
    # may have shifted).
    base_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("post_versions.id", ondelete="SET NULL"), nullable=True
    )
    base_version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Snapshot of Post.version at the time this revision was submitted.",
    )

    # ── Content ────────────────────────────────────────────────────────────
    proposed_markdown: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full proposed markdown body (not a patch). Diff is derived.",
    )
    summary: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Required one-line description of the change (like a commit message).",
    )
    # Nullable derived cache — safe to recompute from base_version + proposed_markdown.
    diff_cache: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Cached unified diff. Recomputed on accept/display; not authoritative.",
    )

    # ── Status ─────────────────────────────────────────────────────────────
    status: Mapped[RevisionStatus] = mapped_column(
        Enum(RevisionStatus, name="revision_status"),
        nullable=False,
        default=RevisionStatus.pending,
        server_default=RevisionStatus.pending.value,
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejection_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Contribution identity snapshot ─────────────────────────────────────
    # Captured at submission time from the author's then-current privacy
    # settings.  Allows the public display of revisions to honour the
    # identity mode that was active when the revision was submitted, even
    # if the author later changes their settings.
    public_identity_mode: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Identity mode at submission time: public|pseudonymous|anonymous",
    )
    public_display_name_snapshot: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Display name or pseudonym captured at submission time.",
    )
    public_avatar_snapshot: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Avatar URL captured at submission time (may be None for anonymous).",
    )

    # ── AI source attribution ───────────────────────────────────────────────
    # Populated only when the revision was generated from an AI suggestion.
    # Format: {"source": "ai_suggestion", "ai_review_request_id": int, "suggestion_id": str}
    source_metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            'Source attribution for AI-generated revisions: '
            '{"source": "ai_suggestion", "ai_review_request_id": int, "suggestion_id": str}.'
        ),
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped[Post] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Post", back_populates="revisions"
    )
    author: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[author_id], back_populates="revisions"
    )
    reviewed_by: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[reviewed_by_id]
    )
    base_version: Mapped[PostVersion | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "PostVersion", foreign_keys=[base_version_id]
    )

    __table_args__ = (
        Index("ix_revisions_post_status", "post_id", "status"),
        Index("ix_revisions_author", "author_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Revision id={self.id} post_id={self.post_id} "
            f"status={self.status.value!r} author_id={self.author_id}>"
        )
