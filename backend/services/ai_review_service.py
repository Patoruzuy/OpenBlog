"""AI Review Service — workspace-scoped async document analysis.

Public API
----------
``request_review(user, post, revision, review_type)``
    Enforce permissions, rate-limit, dedup, enqueue task.

``get_latest_reviews_for_post(post_id, limit)``
    Return the N most recent completed requests for a post.

``get_review(request_id, user)``
    Load a single review request (with permission check).

``cancel_review(request_id, user)``
    Cancel a queued/running request if the caller has permission.

Workspace-only contract
------------------------
All requests *must* have a non-NULL ``workspace_id``.  The service will
raise :class:`AIReviewError` (403-equivalent) if called without one.
This enforcement lives here, not in the DB, so future public access can be
added without a schema change.

Rate limiting
--------------
Redis key ``ai_rl:{user_id}:{workspace_id}:{YYYY-MM-DD}`` (UTC date).
``INCR`` + ``EXPIREAT`` set to the next UTC midnight.  The counter is
compared against ``AI_REVIEWS_DAILY_LIMIT`` (default 10).  When
``fakeredis`` is used in tests the same logic applies without a live broker.

Dedup / idempotency
--------------------
SHA-256 of ``json.dumps(payload, sort_keys=True)`` where payload contains:

    {
        "post_id": int,
        "revision_id": int | None,
        "review_type": str,
        "content_prefix": first 1024 chars of normalised input text
    }

If an existing non-failed request with the same fingerprint exists within
the configured dedup window (default 7 days), the service returns that
existing request instead of creating a new one.  Failed/canceled requests
are NOT deduped — they should always be retriable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from flask import current_app
from sqlalchemy import select

from backend.extensions import db
from backend.models.ai_review import AIReviewRequest, AIReviewResult, AIReviewStatus, AIReviewType
from backend.models.post import Post
from backend.models.revision import Revision
from backend.models.workspace import WorkspaceMember

if TYPE_CHECKING:
    from backend.models.user import User


# ── Domain error ──────────────────────────────────────────────────────────────


class AIReviewError(Exception):
    """Raised by the service for business-rule violations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── Internal helpers ──────────────────────────────────────────────────────────


def _get_redis():
    """Return the Redis client stored in ``current_app.extensions``."""
    return current_app.extensions.get("redis")


def _rate_limit_key(user_id: int, workspace_id: int, today: date) -> str:
    return f"ai_rl:{user_id}:{workspace_id}:{today.isoformat()}"


def _next_midnight_utc() -> int:
    """Return seconds until next UTC midnight as an integer TTL."""
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((tomorrow - now).total_seconds())


def _check_rate_limit(user_id: int, workspace_id: int) -> None:
    """Raise :class:`AIReviewError` (429) when the daily limit is exceeded.

    Uses Redis INCR so the check-and-increment is atomic.
    """
    redis = _get_redis()
    if redis is None:
        # Graceful degradation: no Redis client means no rate limiting.
        return

    limit: int = current_app.config.get("AI_REVIEWS_DAILY_LIMIT", 10)
    key = _rate_limit_key(user_id, workspace_id, datetime.now(UTC).date())

    count = redis.incr(key)
    if count == 1:
        # First request today — set TTL to expire at midnight UTC.
        redis.expire(key, _next_midnight_utc())

    if count > limit:
        raise AIReviewError(
            f"Daily AI review limit reached ({limit}/day per workspace). "
            "Try again tomorrow.",
            status_code=429,
        )


def _compute_fingerprint(
    post_id: int,
    revision_id: int | None,
    review_type: str,
    content_prefix: str,
) -> str:
    """Return a stable SHA-256 hex digest for the given review inputs."""
    payload = {
        "post_id": post_id,
        "revision_id": revision_id,
        "review_type": review_type,
        "content_prefix": content_prefix[:1024],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _get_input_text(post: Post, revision: Revision | None) -> str:
    """Return the text that will be sent to the AI provider.

    If a revision is supplied, the unified diff between the current post body
    and the proposed markdown is used (preferred — gives the AI focused scope).
    Otherwise the current post body is used.
    """
    if revision is not None:
        from backend.utils.diff import compute_diff  # noqa: PLC0415

        diff = compute_diff(
            post.markdown_body or "",
            revision.proposed_markdown or "",
        )
        # Fall back to full proposed text if there is no diff (shouldn't happen).
        return diff if diff.strip() else (revision.proposed_markdown or "")

    return post.markdown_body or ""


def _dedup_lookup(
    fingerprint: str,
    dedup_window_seconds: int,
) -> AIReviewRequest | None:
    """Return an existing non-failed request with the same fingerprint, or None.

    Only requests created within *dedup_window_seconds* and in a non-failed,
    non-canceled status are considered.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=dedup_window_seconds)
    return db.session.scalar(
        select(AIReviewRequest)
        .where(
            AIReviewRequest.input_fingerprint == fingerprint,
            AIReviewRequest.status.in_(
                [
                    AIReviewStatus.queued.value,
                    AIReviewStatus.running.value,
                    AIReviewStatus.completed.value,
                ]
            ),
            AIReviewRequest.created_at >= cutoff,
        )
        .order_by(AIReviewRequest.created_at.desc())
        .limit(1)
    )


def _assert_workspace_member(user: "User", post: Post) -> None:
    """Raise AIReviewError (403-encoded-as-404) if user is not a workspace member."""
    if post.workspace_id is None:
        raise AIReviewError(
            "AI reviews are workspace-only in v1. "
            "This post does not belong to a workspace.",
            status_code=404,
        )
    member = db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == post.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if member is None:
        raise AIReviewError("Not found.", status_code=404)


# ── Public API ────────────────────────────────────────────────────────────────


def request_review(
    user: "User",
    post: Post,
    revision: Revision | None = None,
    review_type: str = AIReviewType.full.value,
) -> AIReviewRequest:
    """Create (or return an existing) AI review request.

    Steps
    -----
    1.  Validate feature is enabled.
    2.  Validate ``review_type`` is one of the supported values.
    3.  Enforce workspace membership (404 for non-members / public posts).
    4.  Enforce daily rate limit per user per workspace (429 on excess).
    5.  Compute input fingerprint and check dedup window.
        a.  If a non-failed request with the same fingerprint exists (within 7
            days): return it without creating a new request.
    6.  Create ``AIReviewRequest`` with status=queued.
    7.  Commit (the Celery task reads from DB, so row must exist first).
    8.  Enqueue ``tasks.run_ai_review``.

    Parameters
    ----------
    user:
        The requesting :class:`~backend.models.user.User`.
    post:
        The :class:`~backend.models.post.Post` to review.
    revision:
        Optional :class:`~backend.models.revision.Revision`; if supplied,
        the diff from ``post.markdown_body`` to ``revision.proposed_markdown``
        is reviewed.
    review_type:
        One of ``clarity``, ``security``, ``architecture``, ``full``.

    Returns
    -------
    AIReviewRequest
        A new (status=queued) or deduped (any status) request.

    Raises
    ------
    AIReviewError 404
        Feature disabled, post not in a workspace, or user not a member.
    AIReviewError 400
        Invalid review_type.
    AIReviewError 429
        Daily rate limit exceeded.
    """
    # 1. Feature gate.
    if not current_app.config.get("AI_REVIEWS_ENABLED", True):
        raise AIReviewError("AI reviews are not enabled.", status_code=404)

    # 2. Validate review type.
    valid_types = AIReviewType.values()
    if review_type not in valid_types:
        raise AIReviewError(
            f"Invalid review_type {review_type!r}. Choose from: {', '.join(valid_types)}",
            status_code=400,
        )

    # 3. Workspace membership gate.
    _assert_workspace_member(user, post)

    # 4. Rate limit.
    _check_rate_limit(user.id, post.workspace_id)  # type: ignore[arg-type]

    # 5. Fingerprint + dedup.
    input_text = _get_input_text(post, revision)
    max_chars: int = current_app.config.get("AI_MAX_INPUT_CHARS", 32768)
    input_text = input_text[:max_chars]

    fingerprint = _compute_fingerprint(
        post_id=post.id,
        revision_id=revision.id if revision else None,
        review_type=review_type,
        content_prefix=input_text,
    )

    dedup_window: int = current_app.config.get(
        "AI_REVIEWS_DEDUP_WINDOW_SECONDS", 7 * 24 * 3600
    )
    existing = _dedup_lookup(fingerprint, dedup_window)
    if existing is not None:
        return existing

    # 6. Create request.
    req = AIReviewRequest(
        workspace_id=post.workspace_id,
        post_id=post.id,
        revision_id=revision.id if revision else None,
        requested_by_user_id=user.id,
        review_type=review_type,
        status=AIReviewStatus.queued.value,
        priority=0,
        input_fingerprint=fingerprint,
        created_at=datetime.now(UTC),
    )
    db.session.add(req)

    # 7. Commit so the Celery task can load the row.
    db.session.commit()

    # 8. Enqueue.
    from backend.tasks.ai_reviews import run_ai_review  # noqa: PLC0415

    run_ai_review.delay(req.id)

    return req


def get_latest_reviews_for_post(
    post_id: int,
    limit: int = 5,
) -> list[AIReviewRequest]:
    """Return the N most recent review requests for *post_id* (all statuses).

    Results are ordered newest-first.  Does **not** check permissions — callers
    must gate access with their own membership check.
    """
    return list(
        db.session.scalars(
            select(AIReviewRequest)
            .where(AIReviewRequest.post_id == post_id)
            .order_by(AIReviewRequest.created_at.desc())
            .limit(limit)
        ).all()
    )


def get_review(request_id: int, user: "User") -> AIReviewRequest:
    """Load a single review request, enforcing workspace membership.

    Returns the :class:`AIReviewRequest` on success.  Raises
    :class:`AIReviewError` (404) when not found or the caller has no access.
    """
    req = db.session.get(AIReviewRequest, request_id)
    if req is None:
        raise AIReviewError("AI review not found.", status_code=404)

    # Load the post to check workspace membership.
    post = db.session.get(Post, req.post_id)
    if post is None:
        raise AIReviewError("AI review not found.", status_code=404)

    _assert_workspace_member(user, post)
    return req


def cancel_review(request_id: int, user: "User") -> AIReviewRequest:
    """Cancel a queued or running review.

    Permission rule: requester OR workspace editor/owner OR platform admin.
    Only ``queued`` and ``running`` requests can be canceled.

    Raises
    ------
    AIReviewError 404
        Request not found or caller has no workspace access.
    AIReviewError 403
        Caller is not the requester and does not hold editor+ role.
    AIReviewError 400
        Request is not in a cancelable state.
    """
    req = get_review(request_id, user)

    if req.status not in (AIReviewStatus.queued.value, AIReviewStatus.running.value):
        raise AIReviewError(
            f"Cannot cancel a review in {req.status!r} state.",
            status_code=400,
        )

    # Permission: requester or editor+
    is_requester = req.requested_by_user_id == user.id
    is_admin = getattr(user, "role", None) is not None and user.role.value == "admin"

    if not is_requester and not is_admin:
        # Check workspace editor+
        member = db.session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == req.workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        from backend.models.workspace import WorkspaceMemberRole  # noqa: PLC0415

        if member is None or not member.role.meets(WorkspaceMemberRole.editor):
            raise AIReviewError(
                "Only the requester or workspace editors/owners can cancel reviews.",
                status_code=403,
            )

    req.status = AIReviewStatus.canceled.value
    req.completed_at = datetime.now(UTC)
    db.session.commit()
    return req
