"""Prompt Analytics Explanation Service.

Public API
----------
``build_input(post, workspace, kind)``
    Build a compact, bounded dict of analytics metrics for the given kind.

``compute_fingerprint(input_dict)``
    Return a stable SHA-256 hex digest of the input payload.

``request_explanation(user, post, workspace, kind, version=None)``
    Enforce scope permissions, dedup by fingerprint, enqueue Celery task.

``get_explanation(user, post, workspace, kind, version=None)``
    Return the most recent completed explanation for the given parameters, or None.

Scope contract
--------------
- Public (workspace=None): caller must be authenticated (raises ExplainError 401).
- Workspace: caller must be a workspace member (raises ExplainError 404).

This is intentionally more permissive than AI reviews (which are workspace-only).
Public explanation generation is auth-gated to prevent abuse but does not
require workspace membership.

Dedup strategy
--------------
SHA-256 of ``json.dumps(input_dict, sort_keys=True)``.  The UNIQUE constraint
on (scope_type, workspace_id, prompt_post_id, prompt_version, kind, input_fingerprint)
provides DB-level enforcement; the service additionally performs a pre-insert
check to return the existing row immediately on duplicate (avoiding the DB error).

Failed rows are NOT deduped — they remain retriable.

Input truncation
----------------
- ``trend`` input JSON: capped at 2 000 chars.
- ``fork_rationale`` input JSON: capped at 2 000 chars.
- ``version_diff`` diff string: capped at 4 000 chars before JSON encoding.
- Overall payload sent to the task: capped at AI_MAX_INPUT_CHARS (default 32 768).
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.extensions import db
from backend.models.analytics_explanation import (
    AnalyticsExplanation,
    AnalyticsExplanationKind,
    AnalyticsExplanationStatus,
)

if TYPE_CHECKING:
    from backend.models.post import Post
    from backend.models.user import User
    from backend.models.workspace import Workspace

# ── Domain error ──────────────────────────────────────────────────────────────

_MAX_DIFF_CHARS = 4_000


class ExplainError(Exception):
    """Raised by the service for business-rule violations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── Scope helpers ─────────────────────────────────────────────────────────────


def _assert_scope(user: User | None, post: Post, workspace: Workspace | None) -> None:
    """Raise ExplainError if the caller is not authorised for this scope."""
    if workspace is None:
        # Public context: require authentication only.
        if user is None:
            raise ExplainError(
                "You must be logged in to generate analytics explanations.",
                status_code=401,
            )
        # Post must actually be a public post (defence in depth).
        if post.workspace_id is not None:
            raise ExplainError(
                "Cannot access a workspace post via the public explanation route.",
                status_code=404,
            )
    else:
        # Workspace context: require membership (fail-closed — 404 not 403).
        from backend.services import workspace_service as ws_svc  # noqa: PLC0415

        if not ws_svc.user_has_workspace_access(user, workspace):
            raise ExplainError(
                "You are not a member of this workspace.",
                status_code=404,
            )


# ── Input builders ────────────────────────────────────────────────────────────


def _trim_json(obj: object, max_chars: int) -> str:
    """Serialise *obj* to compact JSON and truncate to *max_chars*."""
    raw = json.dumps(obj, sort_keys=True, default=str)
    return raw[:max_chars]


def build_input(
    post: Post,
    workspace: Workspace | None,
    kind: str,
) -> dict:
    """Build a compact analytics input dict suitable for explanation generation.

    The dict is **bounded** (JSON ≤ 2 000 chars for trend/fork, diff ≤ 4 000
    chars for version_diff) so the AI provider always receives a safe payload.

    Parameters
    ----------
    post:
        The prompt post whose analytics will be explained.
    workspace:
        ``None`` for public context; ``Workspace`` instance for workspace context.
    kind:
        One of ``'trend'``, ``'fork_rationale'``, ``'version_diff'``.

    Returns
    -------
    dict
        Bounded input dict.  Keys and structure vary by *kind*.
    """
    from backend.services.prompt_analytics_service import (  # noqa: PLC0415
        build_fork_comparison,
        build_version_metrics,
        compute_trend_label,
    )

    if kind == AnalyticsExplanationKind.trend.value:
        metrics = build_version_metrics(post, workspace=workspace)
        trend_label = compute_trend_label(metrics)

        # Only send the last 5 versions; trim each to essential scalar fields.
        trimmed_metrics = [
            {
                "version": m.version,
                "benchmark_avg": m.benchmark_avg,
                "rating_delta": m.rating_delta,
                "ab_wins": m.ab_wins,
                "ab_losses": m.ab_losses,
                "delta_benchmark": m.delta_benchmark,
            }
            for m in metrics[-5:]
        ]
        deltas = [
            m.delta_benchmark for m in metrics[-2:] if m.delta_benchmark is not None
        ]

        payload: dict = {
            "kind": kind,
            "post_id": post.id,
            "trend_label": trend_label,
            "version_metrics": trimmed_metrics,
            "last_2_deltas": deltas,
        }
        # Enforce JSON size cap.
        payload_str = _trim_json(payload, 2_000)
        return json.loads(payload_str) if len(json.dumps(payload)) > 2_000 else payload

    if kind == AnalyticsExplanationKind.fork_rationale.value:
        comparison = build_fork_comparison(post, workspace=workspace)
        entries = [
            {
                "slug": e.slug,
                "is_origin": e.is_origin,
                "composite_score": e.composite_score,
                "benchmark_avg": e.benchmark_avg,
                "vote_count": e.vote_count,
                "ab_win_rate": e.ab_win_rate,
            }
            for e in comparison.entries[:5]
        ]
        payload = {
            "kind": kind,
            "post_id": post.id,
            "fork_count": comparison.fork_count,
            "origin_score": comparison.origin_score,
            "best_fork_score": comparison.best_fork_score,
            "top_entries": entries,
        }
        payload_str = _trim_json(payload, 2_000)
        return json.loads(payload_str) if len(json.dumps(payload)) > 2_000 else payload

    if kind == AnalyticsExplanationKind.version_diff.value:
        from backend.extensions import db as _db  # noqa: PLC0415
        from backend.models.post_version import PostVersion  # noqa: PLC0415
        from backend.utils.diff import compute_diff  # noqa: PLC0415

        versions = list(
            _db.session.scalars(
                select(PostVersion)
                .where(PostVersion.post_id == post.id)
                .order_by(PostVersion.version_number.desc())
                .limit(2)
            ).all()
        )
        # versions is ordered desc: [latest, prev]
        if len(versions) >= 2:
            latest_pv, prev_pv = versions[0], versions[1]
            diff_str = compute_diff(
                prev_pv.markdown_body or "",
                latest_pv.markdown_body or "",
            )
            # Hard cap: 4 000 chars.
            diff_str = diff_str[:_MAX_DIFF_CHARS]
            from_ver = prev_pv.version_number
            to_ver = latest_pv.version_number
        else:
            diff_str = ""
            from_ver = 1
            to_ver = post.version

        payload = {
            "kind": kind,
            "post_id": post.id,
            "from_version": from_ver,
            "to_version": to_ver,
            "diff": diff_str,
        }
        return payload

    raise ExplainError(f"Unknown explanation kind: {kind!r}", status_code=400)


# ── Fingerprint ───────────────────────────────────────────────────────────────


def compute_fingerprint(input_dict: dict) -> str:
    """Return a stable SHA-256 hex digest of *input_dict*.

    Uses ``json.dumps(sort_keys=True)`` for a deterministic serialisation so
    that the same metrics always produce the same fingerprint regardless of
    insertion order.
    """
    encoded = json.dumps(input_dict, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ── Dedup lookup ──────────────────────────────────────────────────────────────


def _dedup_lookup(
    scope_type: str,
    workspace_id: int | None,
    prompt_post_id: int,
    prompt_version: int | None,
    kind: str,
    fingerprint: str,
) -> AnalyticsExplanation | None:
    """Return an existing non-failed explanation with the same fingerprint, or None.

    Only ``queued``, ``running``, and ``completed`` rows are considered.
    Failed rows are intentionally excluded so they are always retriable.
    """
    return db.session.scalar(
        select(AnalyticsExplanation)
        .where(
            AnalyticsExplanation.scope_type == scope_type,
            AnalyticsExplanation.workspace_id == workspace_id,
            AnalyticsExplanation.prompt_post_id == prompt_post_id,
            AnalyticsExplanation.prompt_version == prompt_version,
            AnalyticsExplanation.kind == kind,
            AnalyticsExplanation.input_fingerprint == fingerprint,
            AnalyticsExplanation.status.in_(
                [
                    AnalyticsExplanationStatus.queued.value,
                    AnalyticsExplanationStatus.running.value,
                    AnalyticsExplanationStatus.completed.value,
                ]
            ),
        )
        .order_by(AnalyticsExplanation.created_at.desc())
        .limit(1)
    )


# ── Public API ────────────────────────────────────────────────────────────────


def request_explanation(
    user: User | None,
    post: Post,
    workspace: Workspace | None,
    kind: str,
    version: int | None = None,
) -> AnalyticsExplanation:
    """Validate, dedup, create a row, and enqueue the explanation task.

    Parameters
    ----------
    user:
        Authenticated user (required for both public and workspace contexts).
    post:
        The prompt post to explain.
    workspace:
        ``None`` for public scope; ``Workspace`` for workspace scope.
    kind:
        One of the values in ``AnalyticsExplanationKind``.
    version:
        Optional: tie the explanation to a specific version number.

    Returns
    -------
    AnalyticsExplanation
        The newly created (or existing dedup'd) row.
    """
    # 1. Validate kind.
    if kind not in AnalyticsExplanationKind.values():
        raise ExplainError(
            f"Invalid explanation kind {kind!r}. "
            f"Must be one of: {AnalyticsExplanationKind.values()}",
            status_code=400,
        )

    # 2. Scope gate.
    _assert_scope(user, post, workspace)

    # 3. Build input + fingerprint.
    input_dict = build_input(post, workspace, kind)
    fingerprint = compute_fingerprint(input_dict)

    scope_type = "workspace" if workspace is not None else "public"
    workspace_id = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    # 4. Dedup check.
    existing = _dedup_lookup(
        scope_type=scope_type,
        workspace_id=workspace_id,
        prompt_post_id=post.id,
        prompt_version=version,
        kind=kind,
        fingerprint=fingerprint,
    )
    if existing is not None:
        return existing

    # 5. Create the row.
    row = AnalyticsExplanation(
        scope_type=scope_type,
        workspace_id=workspace_id,
        prompt_post_id=post.id,
        prompt_version=version,
        kind=kind,
        status=AnalyticsExplanationStatus.queued.value,
        input_fingerprint=fingerprint,
        explanation_md=None,
        error_message=None,
        created_by_user_id=user.id,  # type: ignore[union-attr]
    )
    db.session.add(row)
    db.session.commit()

    # 6. Enqueue the Celery task.
    from backend.tasks.analytics_explanations import (
        generate_analytics_explanation,  # noqa: PLC0415
    )

    generate_analytics_explanation.delay(row.id)

    return row


def get_explanation(
    user: User | None,
    post: Post,
    workspace: Workspace | None,
    kind: str,
    version: int | None = None,
) -> AnalyticsExplanation | None:
    """Return the most recent completed explanation, or None if none exists.

    Applies the same scope gate as ``request_explanation``.
    """
    if kind not in AnalyticsExplanationKind.values():
        return None

    # Scope gate (read path — same rules as write).
    try:
        _assert_scope(user, post, workspace)
    except ExplainError:
        return None

    scope_type = "workspace" if workspace is not None else "public"
    workspace_id = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    return db.session.scalar(
        select(AnalyticsExplanation)
        .where(
            AnalyticsExplanation.scope_type == scope_type,
            AnalyticsExplanation.workspace_id == workspace_id,
            AnalyticsExplanation.prompt_post_id == post.id,
            AnalyticsExplanation.prompt_version == version,
            AnalyticsExplanation.kind == kind,
            AnalyticsExplanation.status == AnalyticsExplanationStatus.completed.value,
        )
        .order_by(AnalyticsExplanation.created_at.desc())
        .limit(1)
    )


def get_explanation_any_status(
    post: Post,
    workspace: Workspace | None,
    kind: str,
    version: int | None = None,
) -> AnalyticsExplanation | None:
    """Return the most recent non-failed explanation (any in-progress status).

    Used by the template to distinguish 'generating' from 'not yet requested'.
    Does NOT apply a scope gate — callers must perform their own auth check.
    """
    scope_type = "workspace" if workspace is not None else "public"
    workspace_id = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    return db.session.scalar(
        select(AnalyticsExplanation)
        .where(
            AnalyticsExplanation.scope_type == scope_type,
            AnalyticsExplanation.workspace_id == workspace_id,
            AnalyticsExplanation.prompt_post_id == post.id,
            AnalyticsExplanation.prompt_version == version,
            AnalyticsExplanation.kind == kind,
            AnalyticsExplanation.status.in_(
                [
                    AnalyticsExplanationStatus.queued.value,
                    AnalyticsExplanationStatus.running.value,
                    AnalyticsExplanationStatus.completed.value,
                ]
            ),
        )
        .order_by(AnalyticsExplanation.created_at.desc())
        .limit(1)
    )
