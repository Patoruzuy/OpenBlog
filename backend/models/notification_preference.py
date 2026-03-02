"""NotificationPreference model — per-user delivery preferences.

One row per user; created on first preference update.  Absence of a row
means the user has not changed defaults (in-app enabled, email disabled).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class NotificationPreference(db.Model):
    """Per-user notification delivery settings."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    in_app_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    email_digest_frequency: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="none",
        server_default="none",
        comment="One of: none, daily, weekly",
    )

    # ── Digest scheduling fields ───────────────────────────────────────────
    last_digest_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp of the last successful digest delivery.",
    )
    timezone: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="UTC",
        server_default="UTC",
        comment="IANA timezone string, e.g. 'Europe/London'.",
    )
    digest_hour_local: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=9,
        server_default="9",
        comment="Preferred local delivery hour (0–23).",
    )

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
    user: Mapped[User] = relationship("User")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        CheckConstraint(
            "email_digest_frequency IN ('none','daily','weekly')",
            name="ck_notif_prefs_digest_freq",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationPreference user_id={self.user_id} "
            f"in_app={self.in_app_enabled} email={self.email_enabled}>"
        )
