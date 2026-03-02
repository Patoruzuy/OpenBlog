"""AI Review models — async AI-powered document analysis.

Design notes
------------
*   ``AIReviewRequest``   — one row per user-initiated review job.
*   ``AIReviewResult``    — one row per successfully completed job (1:1).

Workspace-only in v1
---------------------
``workspace_id`` is nullable at the DB level so we can later open public-layer
reviews without a schema migration.  The service layer enforces NOT NULL for all
v1 code paths and returns 404 for any request that arrives without a valid
workspace context.

Dedup / idempotency
--------------------
``input_fingerprint`` is a SHA-256 hex digest of a deterministic JSON payload
containing (post_id, revision_id, review_type, normalised_content_prefix).
The service checks for an existing non-failed request with the same fingerprint
within a 7-day window instead of using a DB UNIQUE constraint, so that failed
reviews stay retriable.

Findings schema (findings_json)
--------------------------------
Each element in the list is:

    {
      "severity": "info" | "warn" | "high",
      "category": "clarity" | "architecture" | "security" | "general",
      "message":  "<human-readable explanation>",
      "suggested_fix": "<optional suggestion>"   # may be absent
    }
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db

# ── Enums ─────────────────────────────────────────────────────────────────────


class AIReviewType(str, enum.Enum):
    clarity = "clarity"
    security = "security"
    architecture = "architecture"
    full = "full"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class AIReviewStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class AIProvider(str, enum.Enum):
    mock = "mock"
    openai = "openai"
    ollama = "ollama"


# ── Models ────────────────────────────────────────────────────────────────────


class AIReviewRequest(db.Model):
    """A user-initiated AI review job.

    Lifecycle: queued → running → completed | failed | canceled
    """

    __tablename__ = "ai_review_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Scope ──────────────────────────────────────────────────────────────
    # Nullable at DB level; service enforces NOT NULL for v1.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )

    # ── Subject ────────────────────────────────────────────────────────────
    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # If set: review the proposed revision diff rather than the current body.
    revision_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("revisions.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Reserved for a future "compare two versions" mode; NULL in v1.
    post_version_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_version_to: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Requester ──────────────────────────────────────────────────────────
    requested_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ── Classification ─────────────────────────────────────────────────────
    review_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="clarity | security | architecture | full",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AIReviewStatus.queued.value,
        server_default=AIReviewStatus.queued.value,
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default="0",
        comment="Higher = processed first. Default 0.",
    )

    # ── Dedup ──────────────────────────────────────────────────────────────
    # SHA-256 of normalised (post_id, revision_id, review_type, content_prefix).
    # NOT unique at DB level; dedup is enforced in the service with a time window.
    input_fingerprint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="SHA-256 hex digest used for dedup within a 7-day window.",
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Error ──────────────────────────────────────────────────────────────
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    result: Mapped[AIReviewResult | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AIReviewResult",
        back_populates="request",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_ai_review_req_workspace_time", "workspace_id", "created_at"),
        Index("ix_ai_review_req_post_time", "post_id", "created_at"),
        Index("ix_ai_review_req_status_time", "status", "created_at"),
        Index("ix_ai_review_req_user_time", "requested_by_user_id", "created_at"),
        Index("ix_ai_review_req_fingerprint", "input_fingerprint"),
    )

    def __repr__(self) -> str:
        return (
            f"<AIReviewRequest id={self.id} type={self.review_type!r} "
            f"status={self.status!r} post_id={self.post_id}>"
        )


class AIReviewResult(db.Model):
    """Structured output from a completed AI review job.

    ``findings_json`` is a list of finding dicts (see module docstring).
    ``metrics_json``  is a free-form dict of provider telemetry (token counts,
    latency, model version, etc.).  Safe to log; never contains user content.
    """

    __tablename__ = "ai_review_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 1:1 with the request; also the cascade target.
    request_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ai_review_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # ── Provider metadata ──────────────────────────────────────────────────
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="mock | openai | ollama"
    )
    model_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="e.g. gpt-4.1-mini, ollama/llama3"
    )
    prompt_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="ai-review-v1",
        server_default="ai-review-v1",
        comment="Prompt template version tag for reproducibility.",
    )

    # ── Output ─────────────────────────────────────────────────────────────
    summary_md: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="Short Markdown summary suitable for inline rendering.",
    )
    findings_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
        comment="List of {severity, category, message, suggested_fix?} dicts.",
    )
    metrics_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="Provider telemetry: tokens, latency_ms, etc.",
    )
    suggested_edits_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
        comment=(
            'Structured AI-suggested edits: '
            '{"edits": [{id, title, kind, target_hint, proposed_markdown, rationale}]}.'
        ),
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    request: Mapped[AIReviewRequest] = relationship(
        "AIReviewRequest",
        back_populates="result",
    )

    def __repr__(self) -> str:
        return (
            f"<AIReviewResult id={self.id} request_id={self.request_id} "
            f"provider={self.provider!r}>"
        )
