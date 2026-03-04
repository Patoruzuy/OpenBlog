"""Notification service — subscriptions, event emission, and inbox management.

Public API (service layer)
--------------------------
subscribe(user, target_type, target_id) -> Subscription
    Register interest in a target entity.  Permission-checked:
    workspace docs / workspaces require membership.
    Public posts must be published.

unsubscribe(user, target_type, target_id) -> bool
    Remove a subscription.  Idempotent (no error if not found).

is_subscribed(user, target_type, target_id) -> bool
    Check whether *user* has an active subscription to the target.

emit(event_type, actor_user_id, target_type, target_id, payload_dict)
    Enqueue a Celery fanout task.  Called from service/task code after
    a meaningful event has been committed to the DB.

list_for_user / mark_read / mark_all_read / unread_count
    Inbox read/management operations (backward-compatible with v0 API).

Internal helpers (used by the fanout task)
------------------------------------------
get_recipients(event_type, target_type, target_id, payload) -> set[int]
    Merge subscription watchers with direct participants.

filter_recipients_by_access(recipients, target_type, target_id) -> set[int]
    Remove recipients who can no longer see the target entity.

create_notification_for_user(user_id, event_type, actor_user_id,
                             target_type, target_id, payload) -> Notification | None
    Insert a Notification row; returns None if skipped by dedup.

Permission rules
----------------
- Workspace docs (Post.workspace_id IS NOT NULL): membership required.
- Public posts (Post.workspace_id IS NULL): must be published; any auth user.
- Workspace subscriptions: membership required.
- Other target types: any authenticated user.

Dedup fingerprint strategy
--------------------------
    fingerprint = "{event_type}:{target_type}:{target_id}:{extra}"
where ``extra`` captures a version/revision identifier from the payload so
that a second event for a different version of the same post is NOT
deduplicated against the first.  On task retry the same payload is used, so
the fingerprint is identical and the INSERT is silently skipped.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.notification import Notification
from backend.models.subscription import Subscription

if TYPE_CHECKING:
    from backend.models.user import User

log = logging.getLogger(__name__)

# ── Valid vocabulary ──────────────────────────────────────────────────────────

ALLOWED_TARGET_TYPES: frozenset[str] = frozenset(
    {"workspace", "post", "revision", "user", "tag"}
)

VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "revision.accepted",
        "revision.rejected",
        "revision.created",
        "ai_review.completed",
        "ai_review.failed",
        "post.published",
        "post.version_published",
        "comment.created",
    }
)

# Notification title/body templates keyed by event_type.
_EVENT_TEMPLATES: dict[str, tuple[str, str]] = {
    "revision.accepted": (
        "Your revision was accepted",
        'Your proposed changes to "{post_title}" were accepted.',
    ),
    "revision.rejected": (
        "Your revision was not accepted",
        'Your proposed changes to "{post_title}" were not accepted.',
    ),
    "ai_review.completed": (
        "AI review complete",
        'The AI review of "{post_title}" has finished.',
    ),
    "post.published": (
        "New post published",
        '"{post_title}" has been published.',
    ),
    "post.version_published": (
        "Post updated",
        '"{post_title}" was updated (v{version}).',
    ),
    "comment.created": (
        "New comment",
        'A new comment was posted on "{post_title}".',
    ),
}


# ── Domain error ──────────────────────────────────────────────────────────────


class NotificationError(Exception):
    """Domain error raised by this service."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── Permission helpers ────────────────────────────────────────────────────────


def _require_workspace_membership(user_id: int, workspace_id: int) -> None:
    from backend.models.workspace import WorkspaceMember  # noqa: PLC0415

    member = db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member is None:
        raise NotificationError(
            "You must be a workspace member to subscribe to this content.", 403
        )


def _check_subscribe_permission(user: User, target_type: str, target_id: int) -> None:
    """Raise :class:`NotificationError` if *user* may not subscribe.

    Rules
    -----
    ``post``       Public (workspace_id NULL) → must be published.
                   Workspace doc (workspace_id NOT NULL) → membership required.
    ``workspace``  Membership required.
    Other          Allowed for any authenticated user.
    """
    if target_type == "post":
        from backend.models.post import Post, PostStatus  # noqa: PLC0415

        post = db.session.get(Post, target_id)
        if post is None:
            raise NotificationError("Post not found.", 404)
        if post.workspace_id is not None:
            _require_workspace_membership(user.id, post.workspace_id)
        else:
            if post.status != PostStatus.published:
                raise NotificationError(
                    "Cannot subscribe to a post that is not published.", 400
                )
    elif target_type == "workspace":
        _require_workspace_membership(user.id, target_id)


# ── Subscription CRUD ─────────────────────────────────────────────────────────


def subscribe(user: User, target_type: str, target_id: int) -> Subscription:
    """Subscribe *user* to events on (*target_type*, *target_id*).

    Idempotent: returns the existing subscription if one already exists.

    Raises
    ------
    NotificationError 400  invalid target_type
    NotificationError 404  target not found
    NotificationError 403  membership required (workspace / workspace doc)
    """
    if target_type not in ALLOWED_TARGET_TYPES:
        raise NotificationError(
            f"Invalid target_type {target_type!r}. "
            f"Allowed: {sorted(ALLOWED_TARGET_TYPES)}.",
            400,
        )

    _check_subscribe_permission(user, target_type, target_id)

    existing = db.session.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.target_type == target_type,
            Subscription.target_id == target_id,
        )
    )
    if existing is not None:
        return existing

    sub = Subscription(
        user_id=user.id,
        target_type=target_type,
        target_id=target_id,
    )
    db.session.add(sub)
    db.session.commit()
    return sub


def unsubscribe(user: User, target_type: str, target_id: int) -> bool:
    """Remove a subscription.  Returns ``True`` if deleted, ``False`` if not found."""
    existing = db.session.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.target_type == target_type,
            Subscription.target_id == target_id,
        )
    )
    if existing is None:
        return False
    db.session.delete(existing)
    db.session.commit()
    return True


def is_subscribed(user: User, target_type: str, target_id: int) -> bool:
    """Return ``True`` when *user* has an active subscription to the target."""
    return (
        db.session.scalar(
            select(func.count()).where(
                Subscription.user_id == user.id,
                Subscription.target_type == target_type,
                Subscription.target_id == target_id,
            )
        )
        or 0
    ) > 0


# ── Event emission ────────────────────────────────────────────────────────────


def emit(
    event_type: str,
    actor_user_id: int | None,
    target_type: str,
    target_id: int,
    payload: dict,
) -> None:
    """Enqueue a Celery fanout task for *event_type*.

    This function is cheap and synchronous.  All heavy work (recipient
    resolution, visibility filtering, notification row creation) happens
    inside the Celery task.
    """
    from backend.tasks.notifications import fanout  # noqa: PLC0415

    fanout.delay(
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )


# ── Recipient resolution ──────────────────────────────────────────────────────


def get_recipients(
    event_type: str,
    target_type: str,
    target_id: int,
    payload: dict,
) -> set[int]:
    """Return the set of user IDs that should receive this event.

    Sources
    -------
    1. Subscribers to the direct target (or associated post for revisions).
    2. Workspace subscribers when payload contains ``workspace_id``.
    3. Direct participants (always notified regardless of subscriptions):
       - ``revision.accepted`` / ``revision.rejected`` → revision author
       - ``ai_review.completed`` / ``ai_review.failed`` → review requester
    """
    recipients: set[int] = set()

    # For revision events, notify subscribers to the post (not the revision).
    if target_type == "revision":
        watch_type = "post"
        watch_id = int(payload.get("post_id", target_id))
    else:
        watch_type = target_type
        watch_id = target_id

    subs = db.session.scalars(
        select(Subscription.user_id).where(
            Subscription.target_type == watch_type,
            Subscription.target_id == watch_id,
        )
    ).all()
    recipients.update(subs)

    # Also add workspace-level watchers when workspace is known.
    workspace_id = payload.get("workspace_id")
    if workspace_id:
        ws_subs = db.session.scalars(
            select(Subscription.user_id).where(
                Subscription.target_type == "workspace",
                Subscription.target_id == int(workspace_id),
            )
        ).all()
        recipients.update(ws_subs)

    # Tag subscribers: only for post.published on PUBLIC posts.
    if event_type == "post.published" and target_type == "post":
        from backend.models.post import Post  # noqa: PLC0415

        post = db.session.get(Post, target_id)
        if post is not None and post.workspace_id is None:
            # Resolve tag IDs from payload first (avoids a second SQL round-
            # trip if the caller already included them), then fall back to DB.
            tag_ids: list[int] = [int(t) for t in payload.get("tag_ids", [])]
            if not tag_ids:
                tag_ids = [tag.id for tag in post.tags]
            if tag_ids:
                tag_subs = db.session.scalars(
                    select(Subscription.user_id).where(
                        Subscription.target_type == "tag",
                        Subscription.target_id.in_(tag_ids),
                    )
                ).all()
                recipients.update(tag_subs)

    # Direct participants.
    if event_type in ("revision.accepted", "revision.rejected", "revision.created"):
        author_id = payload.get("revision_author_id")
        if author_id:
            recipients.add(int(author_id))

    if event_type in ("ai_review.completed", "ai_review.failed"):
        requester_id = payload.get("requester_id")
        if requester_id:
            recipients.add(int(requester_id))

    return recipients


def filter_recipients_by_access(
    recipients: set[int],
    target_type: str,
    target_id: int,
) -> set[int]:
    """Remove recipients who can no longer see the target entity.

    Rules
    -----
    ``revision``  → resolve to the revision's post and apply post rules.
    ``post``      Published public → all.
                  Workspace doc → current workspace members only.
                  Unpublished → author only.
    ``workspace`` → current workspace members only.
    Other         → pass through unchanged.
    """
    if not recipients:
        return set()

    from backend.models.post import Post, PostStatus  # noqa: PLC0415
    from backend.models.workspace import WorkspaceMember  # noqa: PLC0415

    # Resolve revision → post.
    if target_type == "revision":
        from backend.models.revision import Revision  # noqa: PLC0415

        rev = db.session.get(Revision, target_id)
        if rev is None:
            return set()
        target_type = "post"
        target_id = rev.post_id

    if target_type == "post":
        post = db.session.get(Post, target_id)
        if post is None:
            return set()

        if post.workspace_id is None:
            # Public post.
            if post.status == PostStatus.published:
                return set(recipients)
            return {post.author_id} & set(recipients)

        # Workspace doc — members only.
        members = set(
            db.session.scalars(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.workspace_id == post.workspace_id,
                    WorkspaceMember.user_id.in_(list(recipients)),
                )
            ).all()
        )
        return members

    if target_type == "workspace":
        members = set(
            db.session.scalars(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.workspace_id == target_id,
                    WorkspaceMember.user_id.in_(list(recipients)),
                )
            ).all()
        )
        return members

    return set(recipients)


# ── Fingerprint + notification creation ───────────────────────────────────────


def compute_fingerprint(
    event_type: str,
    target_type: str,
    target_id: int,
    payload: dict,
) -> str:
    """Return a stable 32-char dedup key for this (event, version) combination."""
    extra = str(
        payload.get("version")
        or payload.get("revision_id")
        or payload.get("request_id")
        or ""
    )
    raw = f"{event_type}:{target_type}:{target_id}:{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _build_notification_text(event_type: str, payload: dict) -> tuple[str, str]:
    """Return *(title, body)* for a notification of *event_type*."""
    template = _EVENT_TEMPLATES.get(event_type)
    if template is None:
        return (event_type, "")
    title_tpl, body_tpl = template
    ctx = {
        "post_title": payload.get("post_title", "a post"),
        "version": payload.get("version", "?"),
    }
    try:
        title = title_tpl.format(**ctx)
    except KeyError:
        title = title_tpl
    body_extra = ""
    if event_type == "revision.rejected" and payload.get("rejection_note"):
        body_extra = f" Reason: {payload['rejection_note']}"
    try:
        body = body_tpl.format(**ctx) + body_extra
    except KeyError:
        body = body_tpl + body_extra
    return title, body


def create_notification_for_user(
    user_id: int,
    event_type: str,
    actor_user_id: int | None,
    target_type: str,
    target_id: int,
    payload: dict,
) -> Notification | None:
    """Insert a Notification row with dedup guard.

    Returns the new :class:`Notification` or ``None`` on duplicate.
    """
    fp = compute_fingerprint(event_type, target_type, target_id, payload)
    title, body = _build_notification_text(event_type, payload)
    legacy_type = event_type.replace(".", "_")  # backward compat

    # Explicit pre-check so tests (which rely on create_all, not migrations)
    # also benefit from dedup without needing the DB unique index.
    existing_fp = db.session.scalar(
        select(Notification.id).where(
            Notification.user_id == user_id,
            Notification.fingerprint == fp,
        )
    )
    if existing_fp is not None:
        log.debug(
            "Notification dedup: skipped %s for user %s (fp=%s) — pre-check",
            event_type,
            user_id,
            fp,
        )
        return None

    notif = Notification(
        user_id=user_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        notification_type=legacy_type,
        target_type=target_type,
        target_id=target_id,
        payload_json=payload,
        payload=json.dumps(payload),
        fingerprint=fp,
        title=title,
        body=body if body else None,
        created_at=datetime.now(UTC),
    )
    db.session.add(notif)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        log.debug(
            "Notification dedup: skipped %s for user %s (fp=%s) — constraint",
            event_type,
            user_id,
            fp,
        )
        return None
    return notif


# ── Inbox management (backward-compatible v0 class API) ───────────────────────


class NotificationService:
    """Static-method service for reading and acknowledging notifications.

    Backward-compatible with pre-subscription code.  New code should prefer
    the module-level functions (subscribe, unsubscribe, emit, …) above.
    """

    # Expose module-level helpers as class attributes so existing callers can
    # use either ``NotificationService.emit(...)`` or the bare function.
    subscribe = staticmethod(subscribe)
    unsubscribe = staticmethod(unsubscribe)
    is_subscribed = staticmethod(is_subscribed)
    emit = staticmethod(emit)
    get_recipients = staticmethod(get_recipients)
    filter_recipients_by_access = staticmethod(filter_recipients_by_access)
    compute_fingerprint = staticmethod(compute_fingerprint)
    create_notification_for_user = staticmethod(create_notification_for_user)

    @staticmethod
    def list_for_user(
        user_id: int,
        *,
        unread_only: bool = False,
        page: int = 1,
        per_page: int = 20,
        target_type: str | None = None,
        target_id: int | None = None,
    ) -> tuple[list[Notification], int]:
        """Return paginated notifications for *user_id*, newest first.

        Parameters
        ----------
        unread_only:
            When True only unread notifications are returned.
        target_type / target_id:
            When both are supplied, restrict to notifications for that
            specific target (used by the threaded / grouped inbox view).
        """
        q = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            q = q.where(Notification.is_read.is_(False))
        if target_type is not None and target_id is not None:
            q = q.where(
                Notification.target_type == target_type,
                Notification.target_id == target_id,
            )
        q = q.order_by(Notification.created_at.desc())

        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        notifications = list(
            db.session.scalars(q.offset((page - 1) * per_page).limit(per_page))
        )
        return notifications, total

    @staticmethod
    def list_grouped_for_user(
        user_id: int,
        *,
        unread_only: bool = False,
    ) -> list[dict]:
        """Return notifications grouped by (target_type, target_id), newest group first.

        Each entry in the returned list is a dict with keys:
          ``latest``       — the most recent :class:`Notification` in the group
          ``count``        — total notifications in the group (up to 500 scanned)
          ``unread_count`` — unread notifications in the group
          ``target_type``  — group dimension
          ``target_id``    — group dimension

        Fetches at most 500 recent notifications to keep memory usage bounded.
        Groups are ordered by the timestamp of their latest notification.
        """
        q = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            q = q.where(Notification.is_read.is_(False))
        q = q.order_by(Notification.created_at.desc())

        all_notifs = list(db.session.scalars(q.limit(500)))

        groups: dict[tuple, dict] = {}
        for n in all_notifs:
            key = (n.target_type, n.target_id)
            if key not in groups:
                groups[key] = {
                    "latest": n,
                    "count": 0,
                    "unread_count": 0,
                    "target_type": n.target_type,
                    "target_id": n.target_id,
                }
            groups[key]["count"] += 1
            if not n.is_read:
                groups[key]["unread_count"] += 1

        return list(groups.values())

    @staticmethod
    def mark_read(notification_id: int, user_id: int) -> Notification:
        """Mark a single notification as read.

        Raises
        ------
        NotificationError 404  notification not found or owned by a different user
        """
        notif = db.session.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        if notif is None:
            raise NotificationError("Notification not found.", 404)

        if not notif.is_read:
            notif.is_read = True
            notif.read_at = datetime.now(UTC)
            db.session.commit()

        return notif

    @staticmethod
    def mark_all_read(user_id: int) -> int:
        """Mark every unread notification for *user_id* as read.

        Returns the number of rows updated.
        """
        result = db.session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=datetime.now(UTC))
        )
        db.session.commit()
        return result.rowcount

    @staticmethod
    def unread_count(user_id: int) -> int:
        """Return the number of unread notifications for *user_id*."""
        return (
            db.session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
            )
            or 0
        )
