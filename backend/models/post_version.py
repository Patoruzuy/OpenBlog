"""PostVersion model — immutable snapshots of accepted post content.

A new PostVersion is created every time a Revision is accepted (Phase 5).
The version_number mirrors Post.version at the time of acceptance.
These records form the full audit trail and allow diff comparisons between
any two versions of a post.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class PostVersion(db.Model):
    """An immutable snapshot of a post's markdown body at a specific version."""

    __tablename__ = "post_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Post reference ─────────────────────────────────────────────────────
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Matches Post.version at the time this snapshot was taken."
    )

    # ── Content snapshot ───────────────────────────────────────────────────
    markdown_body: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Full markdown body as of this version. Immutable after creation."
    )

    # ── Attribution ────────────────────────────────────────────────────────
    # accepted_by is the admin/editor who merged the revision, or the
    # original author for the initial version (version_number == 1).
    accepted_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Optional: the revision that produced this version (null for v1).
    revision_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("revisions.id", ondelete="SET NULL"), nullable=True
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped[Post] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Post", back_populates="versions"
    )
    accepted_by: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[accepted_by_id]
    )

    __table_args__ = (
        UniqueConstraint("post_id", "version_number", name="uq_post_versions_post_version"),
    )

    def __repr__(self) -> str:
        return f"<PostVersion post_id={self.post_id} v={self.version_number}>"
