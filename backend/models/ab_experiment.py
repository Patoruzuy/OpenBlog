"""A/B Experiment models.

Design
------
ABExperiment        — compares two prompt variants (A/B) against a BenchmarkSuite.
ABExperimentRun     — links one experiment to exactly two BenchmarkRuns (one per variant).

Workspace isolation
-------------------
``experiment.workspace_id IS NULL``  → public experiment; visible to all auth users.
``experiment.workspace_id = ws.id``  → workspace experiment; only members may access.

Status lifecycle
----------------
draft → running → completed
              ↓
           canceled

Transitions are enforced in the service layer.
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class ABExperimentStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    completed = "completed"
    canceled = "canceled"


class ABExperiment(db.Model):
    """An A/B comparison of two prompt variants evaluated on a benchmark suite."""

    __tablename__ = "ab_experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL → public; NOT NULL → workspace-scoped.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    suite_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_suites.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Variant A ─────────────────────────────────────────────────────────
    variant_a_prompt_post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_a_version: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Variant B ─────────────────────────────────────────────────────────
    variant_b_prompt_post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_b_version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        Text,
        CheckConstraint(
            "status IN ('draft','running','completed','canceled')",
            name="ck_ab_experiments_status",
        ),
        nullable=False,
        default=ABExperimentStatus.draft.value,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
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

    # ── Relationships ──────────────────────────────────────────────────────
    suite: Mapped[object] = relationship("BenchmarkSuite", foreign_keys=[suite_id])
    variant_a_prompt: Mapped[object] = relationship(
        "Post", foreign_keys=[variant_a_prompt_post_id]
    )
    variant_b_prompt: Mapped[object] = relationship(
        "Post", foreign_keys=[variant_b_prompt_post_id]
    )
    created_by: Mapped[object] = relationship("User", foreign_keys=[created_by_user_id])
    experiment_run: Mapped[ABExperimentRun | None] = relationship(
        "ABExperimentRun",
        back_populates="experiment",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_ab_experiments_workspace_id", "workspace_id"),
        Index("ix_ab_experiments_suite_id", "suite_id"),
        Index("ix_ab_experiments_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<ABExperiment id={self.id} slug={self.slug!r} status={self.status!r}>"


class ABExperimentRun(db.Model):
    """Ties one ABExperiment to exactly two BenchmarkRuns (run_a and run_b)."""

    __tablename__ = "ab_experiment_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ab_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_a_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_b_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    experiment: Mapped[ABExperiment] = relationship(
        "ABExperiment", back_populates="experiment_run"
    )
    run_a: Mapped[object] = relationship(
        "BenchmarkRun", foreign_keys=[run_a_id]
    )
    run_b: Mapped[object] = relationship(
        "BenchmarkRun", foreign_keys=[run_b_id]
    )

    __table_args__ = (
        UniqueConstraint("experiment_id", name="uq_ab_experiment_runs_experiment"),
        CheckConstraint("run_a_id <> run_b_id", name="ck_ab_experiment_runs_distinct"),
        Index("ix_ab_experiment_runs_experiment_id", "experiment_id"),
    )

    def __repr__(self) -> str:
        return f"<ABExperimentRun id={self.id} exp={self.experiment_id}>"
