"""Digest service — build and deliver periodic email digests.

Public API
----------
period_key(frequency, dt) -> str
    Return the stable dedup key for the given frequency and datetime.
    Daily:  '2026-03-02'
    Weekly: '2026-W10'

period_window(frequency, period_key) -> (start, end)
    Convert a period_key back to UTC (start_inclusive, end_exclusive).

build_digest_for_user(user, since, until) -> DigestData | None
    Query notifications for *user* in [since, until) and produce a
    structured digest.  Returns None when there are no eligible
    notifications.  Workspace content is filtered out if the user is
    no longer a member.

record_digest_run(user_id, frequency, period_key, period_start,
                  period_end, count, status, error_message) -> DigestRun
    Persist a digest run record.  Raises IntegrityError if the unique
    (user_id, frequency, period_key) constraint is violated — callers
    should treat this as an idempotency signal and skip re-sending.

send_digest_for_user(user_id, frequency, period_key) -> str
    Top-level entry point used by Celery tasks.  Handles idempotency,
    builds the digest, sends the email, and records the run.
    Returns the status string: 'sent', 'skipped', or 'failed'.

Idempotency
-----------
Before sending, :func:`send_digest_for_user` queries ``digest_runs``
for an existing row with the same ``(user_id, frequency, period_key)``.
If a ``status='sent'`` row exists the call is a no-op.  If a
``status='failed'`` row exists (from a previous attempt) the send is
re-attempted and the existing row is updated.

Access filtering
----------------
Each notification is tested against :func:`_notification_accessible`
which checks current workspace membership for workspace-scoped content.
This prevents leaking information about workspaces the user has left
since the notification was originally created.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from flask import current_app, render_template
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.digest_run import DigestRun
from backend.models.notification import Notification
from backend.models.notification_preference import NotificationPreference

if TYPE_CHECKING:
    from backend.models.user import User

log = logging.getLogger(__name__)


# ── Period key helpers ────────────────────────────────────────────────────────


def period_key(frequency: str, dt: datetime) -> str:
    """Return the stable period identifier for *frequency* at time *dt*.

    >>> period_key('daily', datetime(2026, 3, 2, 9, 0, tzinfo=UTC))
    '2026-03-02'
    >>> period_key('weekly', datetime(2026, 3, 2, 9, 0, tzinfo=UTC))
    '2026-W09'
    """
    if frequency == "daily":
        return dt.strftime("%Y-%m-%d")
    if frequency == "weekly":
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"Unknown frequency {frequency!r}")


def period_window(frequency: str, key: str) -> tuple[datetime, datetime]:
    """Return UTC (start_inclusive, end_exclusive) for *key*.

    Daily  '2026-03-02' → 2026-03-02T00:00Z .. 2026-03-03T00:00Z
    Weekly '2026-W10'   → Monday 2026-03-09T00:00Z .. 2026-03-16T00:00Z
    """
    if frequency == "daily":
        start = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=UTC)
        end = start + timedelta(days=1)
        return start, end
    if frequency == "weekly":
        # e.g. '2026-W10' → ISO week Monday
        year_str, week_str = key.split("-W")
        year, week = int(year_str), int(week_str)
        # ISO 8601: week 1 contains the first Thursday; use %G-%V-%u parsing
        start = datetime.strptime(f"{year}-{week:02d}-1", "%G-%V-%u").replace(tzinfo=UTC)
        end = start + timedelta(weeks=1)
        return start, end
    raise ValueError(f"Unknown frequency {frequency!r}")


# ── Digest data structures ────────────────────────────────────────────────────


@dataclass
class DigestGroup:
    """A group of related notifications for the digest email."""

    event_label: str       # Human-readable, e.g. 'Revision accepted'
    target_type: str
    target_id: int
    target_title: str      # Post title or workspace name
    target_url: str        # Absolute URL to view the target
    count: int             # Number of notifications in this group


@dataclass
class DigestData:
    """All data needed to render and send a digest email."""

    user: "User"
    frequency: str
    since: datetime
    until: datetime
    groups: list[DigestGroup] = field(default_factory=list)
    total_count: int = 0

    @property
    def period_label(self) -> str:
        if self.frequency == "daily":
            return self.since.strftime("%B %d, %Y")
        # Weekly: show range
        end_display = self.until - timedelta(seconds=1)
        return f"{self.since.strftime('%b %d')} – {end_display.strftime('%b %d, %Y')}"

    @property
    def subject(self) -> str:
        from flask import current_app  # noqa: PLC0415

        site = current_app.config.get("SITE_NAME", "OpenBlog")
        if self.frequency == "daily":
            label = self.since.strftime("%b %d")
        else:
            label = self.period_label
        return f"[{site}] Your {self.frequency} digest · {label}"


# ── Access-check helper ───────────────────────────────────────────────────────


def _notification_accessible(notif: Notification) -> bool:
    """Return True if the notification target is still accessible to the user.

    Workspace-scoped content requires current membership.  Deleted or
    unpublished public posts are excluded.  Unknown / legacy notifications
    without a target_type pass through unchecked.
    """
    if not notif.target_type or not notif.target_id:
        return True  # Legacy notifications: include by default.

    if notif.target_type in ("post", "revision"):
        from backend.models.post import Post, PostStatus  # noqa: PLC0415
        from backend.models.workspace import WorkspaceMember  # noqa: PLC0415

        post_id = notif.target_id
        if notif.target_type == "revision":
            from backend.models.revision import Revision  # noqa: PLC0415

            rev = db.session.get(Revision, notif.target_id)
            if rev is None:
                return False
            post_id = rev.post_id

        post = db.session.get(Post, post_id)
        if post is None:
            return False  # Post deleted.

        if post.workspace_id is not None:
            # Workspace doc — check current membership.
            member = db.session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == post.workspace_id,
                    WorkspaceMember.user_id == notif.user_id,
                )
            )
            return member is not None

        # Public post — include if still published.
        return post.status == PostStatus.published

    if notif.target_type == "workspace":
        from backend.models.workspace import WorkspaceMember  # noqa: PLC0415

        member = db.session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == notif.target_id,
                WorkspaceMember.user_id == notif.user_id,
            )
        )
        return member is not None

    return True  # tag, user, or other types: always include.


# ── Target title/URL resolution ───────────────────────────────────────────────

_EVENT_LABEL: dict[str, str] = {
    "revision.accepted": "Revision accepted",
    "revision.rejected": "Revision rejected",
    "revision.created": "New revision",
    "ai_review.completed": "AI review complete",
    "ai_review.failed": "AI review failed",
    "post.published": "Post published",
    "post.version_published": "Post updated",
    "comment.created": "New comment",
}


def _resolve_target(notif: Notification, base_url: str) -> tuple[str, str]:
    """Return (title, absolute_url) for the notification's target entity."""
    if notif.target_type == "post":
        from backend.models.post import Post  # noqa: PLC0415

        post = db.session.get(Post, notif.target_id)
        if post:
            url = f"{base_url}/posts/{post.slug}" if not post.workspace_id else ""
            return post.title or "Untitled post", url

    if notif.target_type == "revision":
        from backend.models.revision import Revision  # noqa: PLC0415

        rev = db.session.get(Revision, notif.target_id)
        if rev:
            from backend.models.post import Post  # noqa: PLC0415

            post = db.session.get(Post, rev.post_id)
            title = post.title if post else "Untitled post"
            url = f"{base_url}/posts/{post.slug}" if post and not post.workspace_id else ""
            return title, url

    if notif.target_type == "workspace":
        from backend.models.workspace import Workspace  # noqa: PLC0415

        ws = db.session.get(Workspace, notif.target_id)
        if ws:
            return ws.name or "Workspace", ""

    # Fall back to payload data.
    title = notif.payload_json.get("post_title", "") if notif.payload_json else ""
    return title or "Activity", ""


# ── Build digest ──────────────────────────────────────────────────────────────


def build_digest_for_user(
    user: "User",
    since: datetime,
    until: datetime,
    frequency: str = "daily",
) -> DigestData | None:
    """Build a :class:`DigestData` for *user* covering notifications in [since, until).

    Returns ``None`` when there are no accessible notifications in the window.

    Filtering
    ---------
    - Only notifications in the half-open interval ``[since, until)`` are included.
    - Notifications for workspace content the user is no longer a member of are
      silently dropped (prevents information leakage after a membership removal).

    Grouping
    --------
    Notifications are grouped by ``(event_type, target_type, target_id)``.
    For each group the most recent notification's target metadata is used.
    Groups are ordered by descending latest-notification timestamp.
    """
    base_url: str = current_app.config.get("PUBLIC_BASE_URL", "") or ""

    # Fetch all notifications in the window.
    rows = list(
        db.session.scalars(
            select(Notification)
            .where(
                Notification.user_id == user.id,
                Notification.created_at >= since,
                Notification.created_at < until,
            )
            .order_by(Notification.created_at.desc())
        )
    )

    # Filter for accessibility (workspace membership checks).
    accessible = [n for n in rows if _notification_accessible(n)]
    if not accessible:
        return None

    # Group by (event_type, target_type, target_id).
    groups_map: dict[tuple, dict] = {}
    for n in accessible:
        key = (n.event_type or n.notification_type, n.target_type, n.target_id)
        if key not in groups_map:
            groups_map[key] = {"latest": n, "count": 0}
        groups_map[key]["count"] += 1

    digest_groups: list[DigestGroup] = []
    for (event_type, target_type, target_id), data in groups_map.items():
        notif = data["latest"]
        title, url = _resolve_target(notif, base_url)
        label = _EVENT_LABEL.get(event_type or "", event_type or "Notification")
        digest_groups.append(
            DigestGroup(
                event_label=label,
                target_type=target_type or "",
                target_id=target_id or 0,
                target_title=title,
                target_url=url,
                count=data["count"],
            )
        )

    return DigestData(
        user=user,
        frequency=frequency,
        since=since,
        until=until,
        groups=digest_groups,
        total_count=len(accessible),
    )


# ── Digest run recording ──────────────────────────────────────────────────────


def record_digest_run(
    *,
    user_id: int,
    frequency: str,
    pkey: str,
    period_start: datetime,
    period_end: datetime,
    count: int,
    status: str,
    error_message: str | None = None,
) -> DigestRun:
    """Upsert a :class:`DigestRun` row.

    On conflict (same user_id+frequency+period_key) the existing row is
    updated so that failed → retry → sent transitions are recorded cleanly.
    """
    existing = db.session.scalar(
        select(DigestRun).where(
            DigestRun.user_id == user_id,
            DigestRun.frequency == frequency,
            DigestRun.period_key == pkey,
        )
    )
    if existing is not None:
        existing.status = status
        existing.notification_count = count
        existing.error_message = error_message
        if status == "sent":
            existing.sent_at = datetime.now(UTC)
        db.session.commit()
        return existing

    run = DigestRun(
        user_id=user_id,
        frequency=frequency,
        period_key=pkey,
        period_start=period_start,
        period_end=period_end,
        notification_count=count,
        status=status,
        sent_at=datetime.now(UTC) if status == "sent" else None,
        error_message=error_message,
    )
    db.session.add(run)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Another worker already inserted — fetch and return it.
        existing = db.session.scalar(
            select(DigestRun).where(
                DigestRun.user_id == user_id,
                DigestRun.frequency == frequency,
                DigestRun.period_key == pkey,
            )
        )
        if existing is not None:
            return existing
        raise
    return run


# ── Top-level send ────────────────────────────────────────────────────────────


def send_digest_for_user(user_id: int, frequency: str, pkey: str) -> str:
    """Build, send, and record a digest for user *user_id*.

    Returns
    -------
    'sent'     Email delivered and run recorded.
    'skipped'  No notifications in the period; run recorded as skipped.
    'already_sent'  A sent run already exists; nothing done.

    Raises
    ------
    Any exception from :func:`~backend.email.mail_service.send_email` is
    caught, the run is recorded as 'failed', and the exception is re-raised
    so the Celery task can retry.
    """
    from backend.models.user import User  # noqa: PLC0415

    user = db.session.get(User, user_id)
    if user is None:
        log.warning("send_digest_for_user: user %s not found; skipping", user_id)
        return "skipped"

    # Idempotency: skip if already sent for this period.
    already = db.session.scalar(
        select(DigestRun).where(
            DigestRun.user_id == user_id,
            DigestRun.frequency == frequency,
            DigestRun.period_key == pkey,
            DigestRun.status == "sent",
        )
    )
    if already is not None:
        log.debug(
            "Digest already sent for user=%s freq=%s period=%s; skipping",
            user_id,
            frequency,
            pkey,
        )
        return "already_sent"

    period_start, period_end = period_window(frequency, pkey)

    # Build digest.
    data = build_digest_for_user(user, period_start, period_end, frequency)

    if data is None or data.total_count == 0:
        record_digest_run(
            user_id=user_id,
            frequency=frequency,
            pkey=pkey,
            period_start=period_start,
            period_end=period_end,
            count=0,
            status="skipped",
        )
        # Update last_digest_sent_at even for skipped digests so the schedule
        # doesn't retry the same empty period repeatedly.
        _touch_last_sent(user_id, frequency)
        return "skipped"

    # Render email.
    base_url: str = current_app.config.get("PUBLIC_BASE_URL", "") or ""
    inbox_url = f"{base_url}/notifications/"
    preferences_url = f"{base_url}/settings/notifications"

    html_body = render_template(
        "email/digest.html",
        user=user,
        frequency=frequency,
        period_label=data.period_label,
        groups=data.groups,
        total_count=data.total_count,
        inbox_url=inbox_url,
        preferences_url=preferences_url,
    )
    text_body = render_template(
        "email/digest.txt",
        user=user,
        frequency=frequency,
        period_start=period_start.strftime("%Y-%m-%d %H:%M UTC"),
        period_end=period_end.strftime("%Y-%m-%d %H:%M UTC"),
        groups=data.groups,
        inbox_url=inbox_url,
        preferences_url=preferences_url,
    )

    # Send email.
    from backend.email.mail_service import send_email  # noqa: PLC0415

    try:
        send_email(
            to=user.email,
            subject=data.subject,
            text_body=text_body,
            html_body=html_body,
        )
    except Exception as exc:
        record_digest_run(
            user_id=user_id,
            frequency=frequency,
            pkey=pkey,
            period_start=period_start,
            period_end=period_end,
            count=data.total_count,
            status="failed",
            error_message=str(exc),
        )
        raise

    # Record success.
    record_digest_run(
        user_id=user_id,
        frequency=frequency,
        pkey=pkey,
        period_start=period_start,
        period_end=period_end,
        count=data.total_count,
        status="sent",
    )
    _touch_last_sent(user_id, frequency)
    return "sent"


def _touch_last_sent(user_id: int, frequency: str) -> None:  # noqa: ARG001
    """Update ``last_digest_sent_at`` on the user's notification preferences."""
    pref = db.session.get(NotificationPreference, user_id)
    if pref is not None:
        pref.last_digest_sent_at = datetime.now(UTC)
        db.session.commit()
