"""Notification model — in-app alerts delivered to users.

Two generations of notification data live in the same table:

Legacy fields (still written by code that pre-dates the subscription MVP)
--------------------------------------------------------------------------
``notification_type``  String key, e.g. ``'revision_accepted'``.
``title``              Human-readable one-liner.
``body``               Optional longer description.
``payload``            JSON *string* carrying type-specific context.

Structured fields (written by the Celery fanout task)
-----------------------------------------------------
``event_type``         Dot-notation key, e.g. ``'revision.accepted'``.
``actor_user_id``      FK to the user who triggered the event (nullable).
``target_type``        Entity kind: ``'post'``, ``'revision'``, ``'workspace'``, etc.
``target_id``          Entity PK.
``payload_json``       JSON *dict* with event-specific extra data.
``fingerprint``        Dedup key; unique per ``(user_id, fingerprint)`` pair.

Backward compatibility
----------------------
The fanout task sets both ``event_type`` *and* ``notification_type``
(normalised ``event_type.replace('.', '_')``) so existing queries that
filter on ``notification_type`` continue to work.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class Notification(db.Model):
    """An in-app notification for a user."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Recipient ────────────────────────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Legacy fields (kept for backward compatibility) ────────────────────
    # Free-form type key, e.g. 'revision_accepted', 'comment_reply'.
    notification_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON string carrying type-specific context (e.g. {"post_slug": "my-post"})
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Structured event fields (written by fanout; all nullable) ──────────
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who triggered the event (may be null for system events).",
    )
    event_type: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Dot-notation event key, e.g. 'revision.accepted'.",
    )
    target_type: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Entity kind: post, revision, workspace, user, tag.",
    )
    target_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Primary key of the target entity.",
    )
    payload_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Event-specific structured data (JSON dict, not string).",
    )
    fingerprint: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Dedup key; UNIQUE per (user_id, fingerprint) when not NULL.",
    )

    # ── Status ─────────────────────────────────────────────────────────────
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        back_populates="notifications",
        foreign_keys="[Notification.user_id]",
    )
    actor: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys="[Notification.actor_user_id]",
    )

    __table_args__ = (Index("ix_notifications_user_read", "user_id", "is_read"),)

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} user_id={self.user_id} "
            f"type={self.notification_type!r} read={self.is_read}>"
        )
