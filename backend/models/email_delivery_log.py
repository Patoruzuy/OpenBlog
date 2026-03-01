"""EmailDeliveryLog model — records every outbound email attempt."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.extensions import db


class EmailDeliveryLog(db.Model):
    """Audit log of every email queued or sent by the application.

    Populated before sending (status="queued"), updated to "sent" or "failed"
    after the Celery task completes.
    """

    __tablename__ = "email_delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    to_email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    # Short key identifying the template, e.g. "verify_email", "newsletter_confirm"
    template_key: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)

    # "queued" | "sent" | "failed"
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", index=True
    )

    # Optional provider-assigned message ID (e.g. SMTP message-ID header)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Error details when status="failed"
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional JSON blob: {"user_id": 3, "post_id": 7, ...}
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
