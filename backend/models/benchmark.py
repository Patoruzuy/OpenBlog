"""Benchmark models — suites, cases, runs, and results.

Design
------
BenchmarkSuite   — a named collection of test cases.
BenchmarkCase    — a single test case: variable inputs + optional expected output.
BenchmarkRun     — one execution of a suite against a specific prompt version.
BenchmarkRunResult — output + optional score for one (run, case) pair.

Workspace isolation
-------------------
``suite.workspace_id IS NULL``  → public suite; visible to all authenticated users.
``suite.workspace_id = ws.id``  → workspace suite; visible only to members.

Cross-workspace runs are prevented by the service layer.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class BenchmarkRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class BenchmarkSuite(db.Model):
    """A named collection of benchmark cases."""

    __tablename__ = "benchmark_suites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL → public suite; NOT NULL → workspace-scoped suite.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    cases: Mapped[list[BenchmarkCase]] = relationship(
        "BenchmarkCase",
        back_populates="suite",
        cascade="all, delete-orphan",
        order_by="BenchmarkCase.id",
    )
    runs: Mapped[list[BenchmarkRun]] = relationship(
        "BenchmarkRun",
        back_populates="suite",
        cascade="all, delete-orphan",
        order_by="BenchmarkRun.created_at.desc()",
    )
    created_by: Mapped[object] = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        Index("ix_benchmark_suites_workspace_id", "workspace_id"),
        Index("ix_benchmark_suites_slug", "slug"),
    )

    def __repr__(self) -> str:
        return f"<BenchmarkSuite id={self.id} slug={self.slug!r}>"


class BenchmarkCase(db.Model):
    """A single test case within a benchmark suite."""

    __tablename__ = "benchmark_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suite_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_suites.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Arbitrary JSON dict of variable→value mappings used to render the prompt.
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_assertions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    suite: Mapped[BenchmarkSuite] = relationship(
        "BenchmarkSuite", back_populates="cases"
    )
    results: Mapped[list[BenchmarkRunResult]] = relationship(
        "BenchmarkRunResult",
        back_populates="case",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_benchmark_cases_suite_id", "suite_id"),)

    def __repr__(self) -> str:
        return f"<BenchmarkCase id={self.id} name={self.name!r}>"


class BenchmarkRun(db.Model):
    """One execution of a benchmark suite against a prompt version."""

    __tablename__ = "benchmark_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suite_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_suites.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt_post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL → run against a public prompt; NOT NULL → workspace-context run.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        CheckConstraint(
            "status IN ('queued','running','completed','failed','canceled')",
            name="ck_benchmark_runs_status",
        ),
        nullable=False,
        default=BenchmarkRunStatus.queued.value,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
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
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────
    suite: Mapped[BenchmarkSuite] = relationship(
        "BenchmarkSuite", back_populates="runs"
    )
    prompt_post: Mapped[object] = relationship("Post", foreign_keys=[prompt_post_id])
    results: Mapped[list[BenchmarkRunResult]] = relationship(
        "BenchmarkRunResult",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="BenchmarkRunResult.id",
    )
    created_by: Mapped[object] = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        Index("ix_benchmark_runs_suite_id", "suite_id"),
        Index("ix_benchmark_runs_prompt_post_id", "prompt_post_id"),
        Index("ix_benchmark_runs_workspace_id", "workspace_id"),
        Index("ix_benchmark_runs_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<BenchmarkRun id={self.id} status={self.status!r}>"


class BenchmarkRunResult(db.Model):
    """Output and optional score for one (run, case) pair."""

    __tablename__ = "benchmark_run_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    output_text: Mapped[str] = mapped_column(Text, nullable=False)
    score_numeric: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    score_details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    run: Mapped[BenchmarkRun] = relationship("BenchmarkRun", back_populates="results")
    case: Mapped[BenchmarkCase] = relationship(
        "BenchmarkCase", back_populates="results"
    )

    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_benchmark_run_results_run_case"),
        Index("ix_benchmark_run_results_run_id", "run_id"),
        Index("ix_benchmark_run_results_case_id", "case_id"),
    )

    def __repr__(self) -> str:
        return f"<BenchmarkRunResult run_id={self.run_id} case_id={self.case_id}>"
