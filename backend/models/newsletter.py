"""NewsletterSubscription model — double opt-in email subscriptions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class NewsletterSubscription(db.Model):
    """Tracks newsletter subscriptions with double opt-in and one-click unsubscribe.

    Tokens are stored as SHA-256 HMAC digests — never the raw token value.
    """

    __tablename__ = "newsletter_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Normalised to lowercase at insertion time.
    email: Mapped[str] = mapped_column(
        String(254), nullable=False, unique=True, index=True
    )

    # Optional link to a registered user account.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ── Status ─────────────────────────────────────────────────────────────
    # "pending" → confirm email sent, not yet clicked
    # "active"  → confirmed subscriber
    # "unsubscribed" → opted out
    # "bounced" → hard bounce recorded
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unsubscribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Tokens (stored as SHA-256 HMAC hashes — never the raw token) ───────
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When the confirm token was issued (for TTL enforcement)
    confirm_token_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Long-lived; rotated on each new subscription cycle
    unsubscribe_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # ── Metadata ───────────────────────────────────────────────────────────
    # How the subscription was initiated — "footer_form" | "settings" | etc.
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Preferred language for newsletter emails ("en" | "es" | …)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="en")

    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User | None] = relationship("User")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = ()
