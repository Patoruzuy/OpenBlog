"""Thread notification delivery service.

Processes comment-created events and delivers:
  - In-app ``Notification`` rows immediately.
  - Transactional emails via ``EmailService`` (respects per-user preferences
    and a per-thread Redis cooldown to avoid inbox flooding on busy threads).

Thread-unsubscribe token design
--------------------------------
Tokens are HMAC-signed with the application ``SECRET_KEY`` via
``itsdangerous.URLSafeSerializer``.  No revocation state is stored in the
database; a token remains valid until the user re-subscribes (which means
they opted back in voluntarily).
"""

from __future__ import annotations

import json
import logging
import re

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select

from backend.extensions import db
from backend.models.comment import Comment
from backend.models.notification import Notification
from backend.models.portal import IdentityMode, UserPrivacySettings
from backend.models.post import Post, PostStatus
from backend.models.user import User
from backend.services.email_service import EmailService
from backend.services.thread_subscription_service import ThreadSubscriptionService

log = logging.getLogger(__name__)

_UNSUB_SALT = "thread-unsubscribe-v1"
_MD_STRIP = re.compile(r"[*_~`#\[\]>]")
_EXCERPT_MAX = 240


class NotificationDeliveryService:
    # ── Token helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _serializer() -> URLSafeSerializer:
        from flask import current_app  # noqa: PLC0415

        return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_UNSUB_SALT)

    @classmethod
    def make_unsubscribe_token(cls, user_id: int, post_id: int) -> str:
        """Return a signed URL-safe token encoding *user_id* and *post_id*."""
        return cls._serializer().dumps({"u": user_id, "p": post_id})

    @classmethod
    def verify_unsubscribe_token(cls, token: str) -> tuple[int, int] | None:
        """Verify *token* and return ``(user_id, post_id)`` or ``None``.

        Returns ``None`` for any tampered, malformed, or structurally wrong token.
        """
        try:
            data = cls._serializer().loads(token)
            return int(data["u"]), int(data["p"])
        except (BadSignature, KeyError, ValueError, TypeError):
            return None

    # ── User preference helpers ───────────────────────────────────────────────

    @staticmethod
    def _wants_thread_emails(privacy: UserPrivacySettings | None) -> bool:
        """True when *privacy* row is absent (default-on) or the flag is set."""
        return privacy is None or bool(privacy.notify_thread_emails)

    @staticmethod
    def _wants_reply_emails(privacy: UserPrivacySettings | None) -> bool:
        """True when *privacy* row is absent (default-on) or the flag is set."""
        return privacy is None or bool(privacy.notify_reply_emails)

    @staticmethod
    def _get_commenter_name(author: User, privacy: UserPrivacySettings | None) -> str:
        """Return a privacy-aware display name for *author*.

        - public       → display_name or username
        - pseudonymous → pseudonymous_alias or "A member"
        - anonymous    → "Someone"
        """
        mode = privacy.default_identity_mode if privacy else IdentityMode.public.value
        if mode == IdentityMode.anonymous.value:
            return "Someone"
        if mode == IdentityMode.pseudonymous.value:
            return (privacy.pseudonymous_alias if privacy else None) or "A member"
        return author.display_name or author.username

    @staticmethod
    def _strip_markdown(text: str, max_len: int = _EXCERPT_MAX) -> str:
        """Strip common Markdown characters and return a truncated excerpt."""
        clean = _MD_STRIP.sub("", text).strip()
        if len(clean) <= max_len:
            return clean
        return clean[:max_len].rstrip() + "\u2026"  # …

    # ── Redis cooldown helpers (thread-level) ─────────────────────────────────

    @staticmethod
    def _cooldown_key(user_id: int, post_id: int) -> str:
        return f"notif:thread:{user_id}:{post_id}"

    @classmethod
    def _is_on_cooldown(cls, redis, user_id: int, post_id: int) -> bool:
        from flask import current_app  # noqa: PLC0415

        cooldown = current_app.config.get("THREAD_NOTIF_COOLDOWN_SECONDS", 900)
        if not cooldown:
            return False
        return bool(redis.exists(cls._cooldown_key(user_id, post_id)))

    @classmethod
    def _set_cooldown(cls, redis, user_id: int, post_id: int) -> None:
        from flask import current_app  # noqa: PLC0415

        cooldown = current_app.config.get("THREAD_NOTIF_COOLDOWN_SECONDS", 900)
        if not cooldown:
            return
        redis.setex(cls._cooldown_key(user_id, post_id), cooldown, "1")

    # ── In-app notification helper ────────────────────────────────────────────

    @staticmethod
    def _create_notification(
        user_id: int,
        notification_type: str,
        title: str,
        body: str,
        payload: dict,
    ) -> None:
        """Insert a Notification row and flush (caller commits)."""
        notif = Notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            body=body,
            payload=json.dumps(payload),
        )
        db.session.add(notif)
        db.session.flush()

    # ── Main entry point ──────────────────────────────────────────────────────

    @classmethod
    def process_comment_created(
        cls,
        post_id: int,
        comment_id: int,
        author_id: int,
        parent_id: int | None,
        body: str,
    ) -> None:
        """Deliver in-app + email notifications after a comment is created.

        Called from the ``notify_thread_comment_created`` Celery task.

        Rules
        -----
        - Only published posts trigger notifications.
        - The comment author never receives their own notification.
        - Thread-subscriber emails are throttled by
          ``THREAD_NOTIF_COOLDOWN_SECONDS`` per ``(user, post)`` pair to avoid
          inbox flooding.  Direct-reply emails are never throttled.
        - When the reply recipient is also a thread subscriber, they receive
          only the more-specific reply email (no duplicate thread email).
        - Email is only sent to users with a verified email address who have
          not opted out of the relevant notification category.
        """
        from flask import current_app  # noqa: PLC0415

        post = db.session.get(Post, post_id)
        if post is None or post.status != PostStatus.published:
            return

        author = db.session.get(User, author_id)
        if author is None:
            return

        author_privacy = db.session.scalar(
            select(UserPrivacySettings).where(UserPrivacySettings.user_id == author_id)
        )
        commenter_name = cls._get_commenter_name(author, author_privacy)
        excerpt = cls._strip_markdown(body)

        site_url = current_app.config.get("SITE_URL", "https://openblog.dev")
        post_url = f"{site_url}/posts/{post.slug}"

        redis = current_app.extensions.get("redis")

        # ── Identify reply recipient (if this is a reply) ─────────────────────
        reply_recipient_id: int | None = None
        if parent_id is not None:
            parent = db.session.get(Comment, parent_id)
            if parent is not None and parent.author_id != author_id:
                reply_recipient_id = parent.author_id

        # ── Thread-subscriber notifications ───────────────────────────────────
        subscriber_ids: set[int] = set(
            ThreadSubscriptionService.get_subscribers(post_id)
        )
        subscriber_ids.discard(author_id)  # no self-notifications
        if reply_recipient_id is not None:
            subscriber_ids.discard(reply_recipient_id)  # gets reply email instead

        for uid in subscriber_ids:
            recipient = db.session.get(User, uid)
            if recipient is None or not recipient.is_active:
                continue

            priv = db.session.scalar(
                select(UserPrivacySettings).where(UserPrivacySettings.user_id == uid)
            )

            # Always create an in-app notification
            cls._create_notification(
                user_id=uid,
                notification_type="thread_new_comment",
                title=f"New comment on \u201c{post.title}\u201d",
                body=f"{commenter_name}: {excerpt[:120]}",
                payload={
                    "post_id": post_id,
                    "comment_id": comment_id,
                    "post_slug": post.slug,
                },
            )

            # Email — honour preference + cooldown + verified address
            if not cls._wants_thread_emails(priv):
                continue
            if not recipient.is_email_verified:
                continue
            if redis and cls._is_on_cooldown(redis, uid, post_id):
                continue

            locale = "en"
            subject = (
                f"Nuevo comentario en \u201c{post.title}\u201d"
                if locale == "es"
                else f"New comment on \u201c{post.title}\u201d"
            )
            unsub_token = cls.make_unsubscribe_token(uid, post_id)
            unsub_url = (
                f"{site_url}/threads/{post.slug}/unsubscribe?token={unsub_token}"
            )

            EmailService.queue(
                to_email=recipient.email,
                subject=subject,
                template_key="thread_new_comment",
                context={
                    "post_title": post.title,
                    "post_url": post_url,
                    "comment_excerpt": excerpt,
                    "commenter_name": commenter_name,
                    "unsubscribe_url": unsub_url,
                },
                locale=locale,
            )

            if redis:
                cls._set_cooldown(redis, uid, post_id)

        # ── Reply-to-you notification ─────────────────────────────────────────
        if reply_recipient_id is not None:
            recipient = db.session.get(User, reply_recipient_id)
            if recipient is not None and recipient.is_active:
                priv = db.session.scalar(
                    select(UserPrivacySettings).where(
                        UserPrivacySettings.user_id == reply_recipient_id
                    )
                )

                cls._create_notification(
                    user_id=reply_recipient_id,
                    notification_type="thread_reply",
                    title=f"{commenter_name} replied to your comment",
                    body=excerpt[:120],
                    payload={
                        "post_id": post_id,
                        "comment_id": comment_id,
                        "post_slug": post.slug,
                    },
                )

                if cls._wants_reply_emails(priv) and recipient.is_email_verified:
                    locale = "en"
                    subject = (
                        f"{commenter_name} respond\u00ed\u00f3 a tu comentario"
                        if locale == "es"
                        else f"{commenter_name} replied to your comment"
                    )
                    unsub_token = cls.make_unsubscribe_token(
                        reply_recipient_id, post_id
                    )
                    unsub_url = f"{site_url}/threads/{post.slug}/unsubscribe?token={unsub_token}"
                    EmailService.queue(
                        to_email=recipient.email,
                        subject=subject,
                        template_key="thread_reply_to_you",
                        context={
                            "post_title": post.title,
                            "post_url": post_url,
                            "comment_excerpt": excerpt,
                            "commenter_name": commenter_name,
                            "unsubscribe_url": unsub_url,
                        },
                        locale=locale,
                    )

        db.session.commit()
