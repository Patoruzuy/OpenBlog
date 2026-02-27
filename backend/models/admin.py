"""Admin system models.

Two tables that power the admin control center:

* ``AuditLog``    — append-only record of privileged actions
* ``SiteSettings``— key/value store for live-configurable site settings
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class AuditLog(db.Model):
    """Append-only record of every admin action.

    Columns
    -------
    actor_id       User who performed the action (None for system/Celery tasks).
    action         Dot-separated category+verb, e.g. ``"post.published"``,
                   ``"user.suspended"``, ``"revision.accepted"``.
    target_type    Kind of object acted on, e.g. ``"post"``, ``"user"``.
    target_id      PK of the target object (may become stale after deletion).
    target_repr    Human-readable snapshot of target at the time of the action.
    before_state   JSON-serialised before-state snapshot (optional).
    after_state    JSON-serialised after-state snapshot (optional).
    ip_address     Source IP of the admin request (optional).
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    actor_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_repr: Mapped[str | None] = mapped_column(String(512), nullable=True)

    before_state: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON snapshot before the change."
    )
    after_state: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON snapshot after the change."
    )

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationship ───────────────────────────────────────────────────────
    actor: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[actor_id], lazy="select"
    )

    __table_args__ = (
        Index("ix_audit_actors_at", "actor_id", "created_at"),
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_action_at", "action", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"actor={self.actor_id} target={self.target_type}/{self.target_id}>"
        )


class SiteSetting(db.Model):
    """Key/value store for live-editable site configuration.

    Values are stored as JSON strings so booleans, numbers, and strings
    are all representable without separate columns.  The ``description``
    and ``group`` columns serve as self-documenting metadata.
    """

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    key: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    # JSON-encoded value; use SiteSettingsService.get/set for type-safe access.
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    group: Mapped[str] = mapped_column(
        String(64), nullable=False, default="general"
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    updated_by_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    updated_by: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[updated_by_id], lazy="select"
    )

    def __repr__(self) -> str:
        return f"<SiteSetting key={self.key!r} group={self.group!r}>"
