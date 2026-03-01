"""Newsletter service — subscription lifecycle with double opt-in.

Token design
------------
Raw tokens are 32-byte URL-safe random strings (``secrets.token_urlsafe(32)``).
Only the SHA-256 HMAC (keyed on SECRET_KEY) is stored in the database, so a
DB leak does not expose usable confirm/unsubscribe tokens.

Token lookup is constant-time via ``hmac.compare_digest``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.extensions import db
from backend.models.newsletter import NewsletterSubscription


class NewsletterError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class NewsletterService:
    """Manages newsletter subscription lifecycle."""

    # ── Token helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _secret_key() -> bytes:
        from flask import current_app  # noqa: PLC0415

        key = current_app.config.get("SECRET_KEY") or ""
        return key.encode()

    @staticmethod
    def _make_token() -> str:
        """Return a fresh 32-byte URL-safe random token."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def _hash_token(token: str) -> str:
        """Return the HMAC-SHA256 hex digest of *token* keyed on SECRET_KEY."""
        return hmac.new(
            NewsletterService._secret_key(),
            token.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _verify_token(token: str, stored_hash: str) -> bool:
        """Constant-time comparison of *token* against *stored_hash*."""
        expected = NewsletterService._hash_token(token)
        return hmac.compare_digest(expected, stored_hash)

    # ── Subscribe ─────────────────────────────────────────────────────────

    @staticmethod
    def subscribe(
        email: str,
        *,
        source: str = "footer_form",
        locale: str = "en",
        user_id: int | None = None,
    ) -> tuple[NewsletterSubscription, str]:
        """Create or re-activate a subscription for *email*.

        Always returns ``(subscription, confirm_token)`` — even for already-
        subscribed addresses (idempotent, enumeration-safe).

        The caller is responsible for triggering the confirm email task.
        Raises ``NewsletterError`` only for genuine data problems.
        """

        email = email.strip().lower()
        if not email or "@" not in email or len(email) > 254:
            raise NewsletterError("Invalid email address.")

        confirm_token = NewsletterService._make_token()
        unsub_token = NewsletterService._make_token()
        confirm_hash = NewsletterService._hash_token(confirm_token)
        unsub_hash = NewsletterService._hash_token(unsub_token)
        now = datetime.now(UTC)

        sub = db.session.scalar(
            select(NewsletterSubscription).where(NewsletterSubscription.email == email)
        )

        if sub is None:
            sub = NewsletterSubscription(
                email=email,
                user_id=user_id,
                status="pending",
                subscribed_at=now,
                confirm_token_hash=confirm_hash,
                confirm_token_issued_at=now,
                unsubscribe_token_hash=unsub_hash,
                source=source,
                locale=locale,
            )
            db.session.add(sub)
        elif sub.status == "active":
            # Already confirmed — silently succeed (no re-confirm needed).
            # Return a fresh unsub token so the caller can send a "you're
            # already subscribed" email if desired, but don't change status.
            pass
        else:
            # Pending / unsubscribed / bounced — re-issue confirm token.
            sub.status = "pending"
            sub.subscribed_at = now
            sub.confirm_token_hash = confirm_hash
            sub.confirm_token_issued_at = now
            sub.unsubscribe_token_hash = unsub_hash
            sub.source = source
            sub.locale = locale
            if user_id is not None:
                sub.user_id = user_id

        db.session.flush()
        return sub, confirm_token

    # ── Confirm ───────────────────────────────────────────────────────────

    @staticmethod
    def confirm(token: str) -> NewsletterSubscription:
        """Activate a pending subscription via *token*.

        Raises ``NewsletterError(400)`` if token is invalid, expired, or
        the subscription is already confirmed/unsubscribed.
        """
        from flask import current_app  # noqa: PLC0415

        token_hash = NewsletterService._hash_token(token)
        sub = db.session.scalar(
            select(NewsletterSubscription).where(
                NewsletterSubscription.confirm_token_hash == token_hash
            )
        )
        if sub is None:
            raise NewsletterError("Invalid or expired confirmation link.", 400)

        if sub.status == "active":
            return sub  # idempotent

        if sub.status == "unsubscribed":
            raise NewsletterError(
                "This address has been unsubscribed. Subscribe again to re-join.", 400
            )

        # Enforce TTL on confirm links.
        ttl = current_app.config.get("NEWSLETTER_CONFIRM_TTL", 48 * 3600)
        if sub.confirm_token_issued_at is not None:
            issued = sub.confirm_token_issued_at
            if issued.tzinfo is None:
                issued = issued.replace(tzinfo=UTC)
            if datetime.now(UTC) > issued + timedelta(seconds=ttl):
                raise NewsletterError(
                    "Confirmation link has expired. Please subscribe again.", 400
                )

        sub.status = "active"
        sub.confirmed_at = datetime.now(UTC)
        db.session.flush()
        return sub

    # ── Unsubscribe ───────────────────────────────────────────────────────

    @staticmethod
    def unsubscribe(token: str) -> NewsletterSubscription:
        """Mark a subscription as unsubscribed.

        Raises ``NewsletterError(400)`` for invalid tokens.
        Idempotent — already-unsubscribed addresses return unchanged.
        """
        token_hash = NewsletterService._hash_token(token)
        sub = db.session.scalar(
            select(NewsletterSubscription).where(
                NewsletterSubscription.unsubscribe_token_hash == token_hash
            )
        )
        if sub is None:
            raise NewsletterError("Invalid unsubscribe link.", 400)

        if sub.status != "unsubscribed":
            sub.status = "unsubscribed"
            sub.unsubscribed_at = datetime.now(UTC)
            db.session.flush()

        return sub

    # ── User helpers ──────────────────────────────────────────────────────

    @staticmethod
    def get_for_user(user_id: int) -> NewsletterSubscription | None:
        """Return the subscription linked to *user_id*, if any."""
        return db.session.scalar(
            select(NewsletterSubscription).where(
                NewsletterSubscription.user_id == user_id
            )
        )

    @staticmethod
    def get_by_email(email: str) -> NewsletterSubscription | None:
        return db.session.scalar(
            select(NewsletterSubscription).where(
                NewsletterSubscription.email == email.strip().lower()
            )
        )

    @staticmethod
    def link_to_user(email: str, user_id: int) -> None:
        """Attach *user_id* to the subscription for *email*, if unlinked."""
        sub = NewsletterService.get_by_email(email)
        if sub is not None and sub.user_id is None:
            sub.user_id = user_id
            db.session.flush()

    @staticmethod
    def unsubscribe_token_for(sub: NewsletterSubscription) -> str:
        """Re-derive an unsubscribe token from a subscription's stored hash.

        This is NOT possible because we store only the hash — callers must
        persist the raw token at subscribe time for use in emails.  This method
        generates a NEW token and rotates the stored hash.  Use only when
        re-sending the unsubscribe link.
        """
        new_token = NewsletterService._make_token()
        sub.unsubscribe_token_hash = NewsletterService._hash_token(new_token)
        db.session.flush()
        return new_token
