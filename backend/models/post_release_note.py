"""PostReleaseNote model — structured changelog entries per post version.

One record is created every time a ``Revision`` is accepted and a new
``PostVersion`` snapshot is written.  The entry stores a human-readable
summary of what changed, the version number it corresponds to, and a
back-reference to the originating revision.

The record is *always* created within the same transaction as the
``PostVersion`` so they are atomically consistent.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class PostReleaseNote(db.Model):
    """A changelog entry for a specific version of a post."""

    __tablename__ = "post_release_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Post reference ─────────────────────────────────────────────────────
    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Version this entry describes ───────────────────────────────────────
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Mirrors PostVersion.version_number / Post.version at acceptance time.",
    )

    # ── Human-readable summary ─────────────────────────────────────────────
    summary: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment=(
            "One-line description of the change.  Set from Revision.summary when "
            "available; auto-generated when auto_generated=True."
        ),
    )

    # ── Attribution ────────────────────────────────────────────────────────
    accepted_revision_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("revisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="The revision whose acceptance created this entry.  NULL for v1 auto-entries.",
    )

    # ── Flags ──────────────────────────────────────────────────────────────
    auto_generated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True when the summary was generated automatically, not supplied by a human.",
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped[Post] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Post",
        back_populates="release_notes",
    )
    accepted_revision: Mapped[Revision | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Revision",
        foreign_keys=[accepted_revision_id],
    )

    def __repr__(self) -> str:
        return f"<PostReleaseNote post_id={self.post_id} v={self.version_number}>"
