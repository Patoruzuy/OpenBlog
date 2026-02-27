"""PinnedPost model — author-pinned posts shown at the top of their profile."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db

_MAX_PINNED = 6  # GitHub-style max of 6 pinned items


class PinnedPost(db.Model):
    """A post pinned by its author (or an admin) to their profile page.

    Maximum *_MAX_PINNED* pinned posts per user — enforced at the service layer.
    """

    __tablename__ = "pinned_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Display position 1–6 (lower = higher on page)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821
    post: Mapped[Post] = relationship("Post")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint("user_id", "post_id", name="uq_pinned_posts_user_post"),
    )
