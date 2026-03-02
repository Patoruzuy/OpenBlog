"""A/B Experiment service — create, start, cancel, and compare experiments.

Scope rules (enforced at service layer, never in routes)
---------------------------------------------------------
Public experiment (workspace_id IS NULL):
  - Visible to any authenticated user.
  - Suite must be public (suite.workspace_id IS NULL).
  - Variants must be public published prompts.
  - Drafts rejected.

Workspace experiment (workspace_id IS NOT NULL):
  - Visible only to workspace members (viewer+).
  - Suite must belong to the SAME workspace.
  - Variants may be public published prompts OR prompts in the SAME workspace.
  - Prompts from a DIFFERENT workspace are always rejected.
  - Drafts rejected unless prompt is in the same workspace and published.
  - Non-members receive None (route converts to 404 — fail-closed).

Run execution:
  - start_experiment() creates two BenchmarkRun rows via benchmark_service.create_run(),
    then enqueues both Celery tasks. The experiment status moves to 'running'.
  - Completion is detected lazily via compute_comparison() by checking run statuses.

Comparison DTO:
  - Per-case dict: {case_id, case_name, output_a, output_b, score_a, score_b}
  - Aggregates: {avg_score_a, avg_score_b, delta, count_total,
                 count_scored_a, count_scored_b, count_matched}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from backend.extensions import db
from backend.models.ab_experiment import (
    ABExperiment,
    ABExperimentRun,
    ABExperimentStatus,
)
from backend.models.benchmark import (
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.post import Post, PostStatus
from backend.models.user import User
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import benchmark_service as bsvc
from backend.services.benchmark_service import BenchmarkError

# Maximum number of cases shown in comparison (pagination can be added later).
_MAX_COMPARISON_CASES = 100


# ── DTOs ──────────────────────────────────────────────────────────────────────


@dataclass
class CaseComparison:
    case_id: int
    case_name: str
    output_a: str | None = None
    output_b: str | None = None
    score_a: Decimal | None = None
    score_b: Decimal | None = None

    @property
    def score_delta(self) -> Decimal | None:
        if self.score_a is not None and self.score_b is not None:
            return self.score_b - self.score_a
        return None


@dataclass
class ExperimentComparison:
    experiment: ABExperiment
    cases: list[CaseComparison] = field(default_factory=list)
    avg_score_a: Decimal | None = None
    avg_score_b: Decimal | None = None
    count_total: int = 0
    count_scored_a: int = 0
    count_scored_b: int = 0
    count_matched: int = 0  # cases with results for BOTH variants

    @property
    def delta(self) -> Decimal | None:
        if self.avg_score_a is not None and self.avg_score_b is not None:
            return self.avg_score_b - self.avg_score_a
        return None

    @property
    def run_a_status(self) -> str | None:
        er = self.experiment.experiment_run
        if er is None:
            return None
        run_a = er.run_a
        return run_a.status if run_a else None  # type: ignore[union-attr]

    @property
    def run_b_status(self) -> str | None:
        er = self.experiment.experiment_run
        if er is None:
            return None
        run_b = er.run_b
        return run_b.status if run_b else None  # type: ignore[union-attr]


# ── Slug helpers ──────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "experiment"


def _unique_slug(base: str) -> str:
    base = base[:80]
    existing = set(
        db.session.scalars(
            select(ABExperiment.slug).where(ABExperiment.slug.like(f"{base}%"))
        ).all()
    )
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


# ── Internal helpers ─────────────────────────────────────────────────────────


def _require_auth(user: User | None) -> User:
    if user is None:
        raise BenchmarkError("Authentication required.", status_code=401)
    return user


def _get_member(workspace: Workspace, user: User) -> WorkspaceMember | None:
    return db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )


def _assert_access(experiment: ABExperiment, user: User) -> None:
    """Raise BenchmarkError(404) if *user* may not access *experiment*."""
    if experiment.workspace_id is None:
        return  # public experiment — any authenticated user
    ws = db.session.get(Workspace, experiment.workspace_id)
    if ws is None or _get_member(ws, user) is None:
        raise BenchmarkError("Experiment not found.", status_code=404)


def _validate_variant(
    prompt: Post,
    *,
    suite: BenchmarkSuite,
    label: str,  # "Variant A" / "Variant B" for error messages
) -> None:
    """Enforce scope rules for one variant prompt against the suite."""
    if prompt.status != PostStatus.published:
        raise BenchmarkError(f"{label}: only published prompts can be benchmarked.", status_code=422)

    if suite.workspace_id is None:
        # Public suite — prompt must also be public.
        if prompt.workspace_id is not None:
            raise BenchmarkError(
                f"{label}: public experiments require public prompts.", status_code=422
            )
    else:
        # Workspace suite — prompt must be in the SAME workspace or be public.
        if prompt.workspace_id is not None and prompt.workspace_id != suite.workspace_id:
            raise BenchmarkError(
                f"{label}: prompt belongs to a different workspace.", status_code=422
            )


# ── Public API ────────────────────────────────────────────────────────────────


def create_experiment(
    user: User | None,
    name: str,
    suite: BenchmarkSuite,
    variant_a_prompt: Post,
    variant_a_version: int,
    variant_b_prompt: Post,
    variant_b_version: int,
    *,
    description: str | None = None,
    workspace: Workspace | None = None,
) -> ABExperiment:
    """Create a new A/B experiment in *draft* status.

    Scope is validated immediately — service raises BenchmarkError on violations.
    """
    user = _require_auth(user)
    name = name.strip()
    if not name:
        raise BenchmarkError("Experiment name cannot be empty.")

    # Workspace membership check.
    if workspace is not None:
        member = _get_member(workspace, user)
        if member is None:
            raise BenchmarkError("Not a workspace member.", status_code=404)
        if not member.role.meets(WorkspaceMemberRole.editor):
            raise BenchmarkError("Editor role required to create experiments.", status_code=403)

    # Suite scope.
    if workspace is None:
        if suite.workspace_id is not None:
            raise BenchmarkError("Public experiments require a public suite.", status_code=422)
    else:
        if suite.workspace_id != workspace.id:
            raise BenchmarkError("Suite does not belong to this workspace.", status_code=422)

    # Variant scope.
    _validate_variant(variant_a_prompt, suite=suite, label="Variant A")
    _validate_variant(variant_b_prompt, suite=suite, label="Variant B")

    # Prevent trivial same-prompt-same-version experiments.
    if (
        variant_a_prompt.id == variant_b_prompt.id
        and variant_a_version == variant_b_version
    ):
        raise BenchmarkError(
            "Variant A and B must differ (different prompt or different version).",
            status_code=422,
        )

    slug = _unique_slug(_slugify(name))
    exp = ABExperiment(
        name=name,
        slug=slug,
        description=description or None,
        workspace_id=workspace.id if workspace is not None else None,
        suite_id=suite.id,
        variant_a_prompt_post_id=variant_a_prompt.id,
        variant_a_version=variant_a_version,
        variant_b_prompt_post_id=variant_b_prompt.id,
        variant_b_version=variant_b_version,
        status=ABExperimentStatus.draft.value,
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    db.session.add(exp)
    db.session.flush()
    return exp


def start_experiment(user: User | None, experiment: ABExperiment) -> ABExperimentRun:
    """Enqueue both benchmark runs and move the experiment to 'running'.

    Delegates to benchmark_service.create_run() for each variant, which
    handles scope re-validation and Celery task dispatch.
    """
    user = _require_auth(user)
    _assert_access(experiment, user)

    if experiment.status != ABExperimentStatus.draft.value:
        raise BenchmarkError(
            f"Cannot start experiment in '{experiment.status}' state.", status_code=422
        )
    if experiment.experiment_run is not None:
        raise BenchmarkError("Experiment already has runs attached.", status_code=422)

    suite = db.session.get(BenchmarkSuite, experiment.suite_id)
    prompt_a = db.session.get(Post, experiment.variant_a_prompt_post_id)
    prompt_b = db.session.get(Post, experiment.variant_b_prompt_post_id)

    if suite is None or prompt_a is None or prompt_b is None:
        raise BenchmarkError("Experiment references missing data.", status_code=500)

    # Re-use benchmark_service.create_run() which handles scope + task dispatch.
    # (create_run derives workspace context from suite.workspace_id internally.)
    run_a = bsvc.create_run(user, suite, prompt_a, experiment.variant_a_version)
    run_b = bsvc.create_run(user, suite, prompt_b, experiment.variant_b_version)
    db.session.flush()

    exp_run = ABExperimentRun(
        experiment_id=experiment.id,
        run_a_id=run_a.id,
        run_b_id=run_b.id,
        created_at=datetime.now(UTC),
    )
    db.session.add(exp_run)

    experiment.status = ABExperimentStatus.running.value
    experiment.started_at = datetime.now(UTC)
    db.session.flush()
    return exp_run


def cancel_experiment(user: User | None, experiment: ABExperiment) -> ABExperiment:
    """Cancel the experiment and any underlying queued/running benchmark runs."""
    user = _require_auth(user)
    _assert_access(experiment, user)

    if experiment.status in (
        ABExperimentStatus.completed.value,
        ABExperimentStatus.canceled.value,
    ):
        raise BenchmarkError(
            f"Cannot cancel experiment in '{experiment.status}' state.", status_code=422
        )

    # Cancel underlying runs.
    if experiment.experiment_run is not None:
        for run in (experiment.experiment_run.run_a, experiment.experiment_run.run_b):
            if run is not None and run.status in (  # type: ignore[union-attr]
                BenchmarkRunStatus.queued.value,
                BenchmarkRunStatus.running.value,
            ):
                try:
                    bsvc.cancel_run(user, run)  # type: ignore[arg-type]
                except BenchmarkError:
                    pass  # already completed/canceled — ignore

    experiment.status = ABExperimentStatus.canceled.value
    experiment.completed_at = datetime.now(UTC)
    db.session.flush()
    return experiment


def get_experiment(
    user: User | None,
    slug: str,
    workspace: Workspace | None = None,
) -> ABExperiment | None:
    """Return the experiment for *slug* in the given scope, or None."""
    if user is None:
        return None

    if workspace is None:
        exp = db.session.scalar(
            select(ABExperiment).where(
                ABExperiment.slug == slug,
                ABExperiment.workspace_id.is_(None),
            )
        )
    else:
        member = _get_member(workspace, user)
        if member is None:
            return None
        exp = db.session.scalar(
            select(ABExperiment).where(
                ABExperiment.slug == slug,
                ABExperiment.workspace_id == workspace.id,
            )
        )
    return exp


def list_experiments(
    user: User | None,
    workspace: Workspace | None = None,
) -> list[ABExperiment]:
    """Return experiments visible to *user* in the given scope."""
    if user is None:
        return []

    if workspace is None:
        stmt = (
            select(ABExperiment)
            .where(ABExperiment.workspace_id.is_(None))
            .order_by(ABExperiment.created_at.desc())
        )
        return list(db.session.scalars(stmt).all())

    member = _get_member(workspace, user)
    if member is None:
        return []

    stmt = (
        select(ABExperiment)
        .where(ABExperiment.workspace_id == workspace.id)
        .order_by(ABExperiment.created_at.desc())
    )
    return list(db.session.scalars(stmt).all())


def _sync_completion(experiment: ABExperiment) -> None:
    """If both underlying runs are done, mark the experiment completed."""
    er = experiment.experiment_run
    if er is None:
        return
    run_a = er.run_a  # type: ignore[union-attr]
    run_b = er.run_b  # type: ignore[union-attr]
    if run_a is None or run_b is None:
        return
    terminal = {BenchmarkRunStatus.completed.value, BenchmarkRunStatus.failed.value, BenchmarkRunStatus.canceled.value}
    if run_a.status in terminal and run_b.status in terminal:  # type: ignore[union-attr]
        if experiment.status == ABExperimentStatus.running.value:
            experiment.status = ABExperimentStatus.completed.value
            experiment.completed_at = datetime.now(UTC)
            db.session.flush()


def compute_comparison(
    user: User | None,
    experiment: ABExperiment,
) -> ExperimentComparison:
    """Build an ExperimentComparison DTO from stored run results.

    Algorithm
    ---------
    1.  Load all BenchmarkRunResult rows for run_a (one query).
    2.  Load all BenchmarkRunResult rows for run_b (one query).
    3.  Load case names for all touched case_ids (one query).
    4.  Join by case_id in Python.
    5.  Compute aggregates.

    Only the first _MAX_COMPARISON_CASES cases are included (bounded).
    """
    user = _require_auth(user)
    _assert_access(experiment, user)

    # Lazily mark as completed if both runs finished.
    _sync_completion(experiment)

    comparison = ExperimentComparison(experiment=experiment)

    er = experiment.experiment_run
    if er is None:
        return comparison  # not started yet

    run_a_id = er.run_a_id
    run_b_id = er.run_b_id

    # ── Load results (two bounded queries) ────────────────────────────────
    results_a: list[BenchmarkRunResult] = list(
        db.session.scalars(
            select(BenchmarkRunResult)
            .where(BenchmarkRunResult.run_id == run_a_id)
            .order_by(BenchmarkRunResult.case_id)
            .limit(_MAX_COMPARISON_CASES)
        ).all()
    )
    results_b: list[BenchmarkRunResult] = list(
        db.session.scalars(
            select(BenchmarkRunResult)
            .where(BenchmarkRunResult.run_id == run_b_id)
            .order_by(BenchmarkRunResult.case_id)
            .limit(_MAX_COMPARISON_CASES)
        ).all()
    )

    # ── Map by case_id ────────────────────────────────────────────────────
    map_a: dict[int, BenchmarkRunResult] = {r.case_id: r for r in results_a}
    map_b: dict[int, BenchmarkRunResult] = {r.case_id: r for r in results_b}

    all_case_ids = sorted(map_a.keys() | map_b.keys())

    # ── Load case names in one query ──────────────────────────────────────
    from backend.models.benchmark import BenchmarkCase  # noqa: PLC0415

    name_map: dict[int, str] = {}
    if all_case_ids:
        cases = db.session.scalars(
            select(BenchmarkCase).where(BenchmarkCase.id.in_(all_case_ids))
        ).all()
        name_map = {c.id: c.name for c in cases}

    # ── Build per-case rows ────────────────────────────────────────────────
    comparison.count_total = len(all_case_ids)
    case_rows: list[CaseComparison] = []
    scores_a: list[Decimal] = []
    scores_b: list[Decimal] = []
    matched = 0

    for cid in all_case_ids:
        ra = map_a.get(cid)
        rb = map_b.get(cid)
        row = CaseComparison(
            case_id=cid,
            case_name=name_map.get(cid, f"Case {cid}"),
            output_a=ra.output_text if ra else None,
            output_b=rb.output_text if rb else None,
            score_a=Decimal(str(ra.score_numeric)) if ra and ra.score_numeric is not None else None,
            score_b=Decimal(str(rb.score_numeric)) if rb and rb.score_numeric is not None else None,
        )
        case_rows.append(row)
        if row.score_a is not None:
            scores_a.append(row.score_a)
        if row.score_b is not None:
            scores_b.append(row.score_b)
        if ra is not None and rb is not None:
            matched += 1

    comparison.cases = case_rows
    comparison.count_scored_a = len(scores_a)
    comparison.count_scored_b = len(scores_b)
    comparison.count_matched = matched
    comparison.avg_score_a = (sum(scores_a) / len(scores_a)) if scores_a else None
    comparison.avg_score_b = (sum(scores_b) / len(scores_b)) if scores_b else None
    return comparison
