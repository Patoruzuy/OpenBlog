"""Analytics Explanation model — stores AI-generated analytics explanations.

Design notes
------------
*   ``AnalyticsExplanation`` — one row per user-initiated explanation job.

Scope
-----
``scope_type`` is either ``'public'`` or ``'workspace'``.
``workspace_id`` is NULL for public explanations and set for workspace ones.
Both values are enforced by the service layer (not just DB constraints).

Status lifecycle
----------------
queued → running → completed | failed

Dedup
-----
``input_fingerprint`` is a SHA-256 hex digest of a deterministic JSON
payload capturing all metrics relevant to the explanation kind.  The service
enforces uniqueness via a UNIQUE constraint on
(scope_type, workspace_id, prompt_post_id, prompt_version, kind, input_fingerprint).
Failed rows are not deduplicated—they remain retriable.

Kind
----
- ``trend``: explains the trend_label (improving/regressing/stable).
- ``fork_rationale``: explains why the best fork ranks highest.
- ``version_diff``: summarises what changed between two prompt versions.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.extensions import db

# ── Enums ─────────────────────────────────────────────────────────────────────


class AnalyticsExplanationKind(str, enum.Enum):
    trend = "trend"
    fork_rationale = "fork_rationale"
    version_diff = "version_diff"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class AnalyticsExplanationStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


# ── Model ─────────────────────────────────────────────────────────────────────


class AnalyticsExplanation(db.Model):
    """A user-initiated AI analytics explanation job.

    Lifecycle: queued → running → completed | failed
    """

    __tablename__ = "analytics_explanations"

    __table_args__ = (
        # Dedup constraint: one completed explanation per (scope, workspace, post,
        # version, kind, fingerprint) combination.
        UniqueConstraint(
            "scope_type",
            "workspace_id",
            "prompt_post_id",
            "prompt_version",
            "kind",
            "input_fingerprint",
            name="uq_analytics_explanation_fingerprint",
        ),
        # Fast lookup by post + workspace ordered by recency.
        Index(
            "ix_analytics_explanation_post_ws_time",
            "prompt_post_id",
            "workspace_id",
            "created_at",
        ),
        # CHECK constraints (validated at DB level for defence in depth).
        CheckConstraint(
            "scope_type IN ('public', 'workspace')",
            name="ck_analytics_explanation_scope_type",
        ),
        CheckConstraint(
            "kind IN ('trend', 'fork_rationale', 'version_diff')",
            name="ck_analytics_explanation_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_analytics_explanation_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Scope ──────────────────────────────────────────────────────────────
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)

    # NULL for public explanations; set for workspace explanations.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )

    # ── Subject ────────────────────────────────────────────────────────────
    prompt_post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Optional: tie explanation to a specific post version.
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Classification ─────────────────────────────────────────────────────
    kind: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=AnalyticsExplanationStatus.queued.value,
        server_default="queued",
    )

    # SHA-256 hex digest of the deterministic input payload.
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Result ─────────────────────────────────────────────────────────────
    # NULL while queued/running; populated on completion.
    explanation_md: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Populated on failure; truncated to 400 chars (AGENTS.md §2.4).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Requester ──────────────────────────────────────────────────────────
    created_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default="CURRENT_TIMESTAMP",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AnalyticsExplanation id={self.id} kind={self.kind!r} "
            f"status={self.status!r} post_id={self.prompt_post_id}>"
        )
