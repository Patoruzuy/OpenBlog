"""Fork Recommendation Service — deterministic optimal-fork scoring.

Given a base prompt, rank its forks by a weighted composite score derived
from four public signals already present in the database.

Scoring model (weights sum to 1.0)
------------------------------------
    score = 0.40 * norm_benchmark_score
          + 0.25 * norm_rating
          + 0.15 * norm_execution_count
          + 0.10 * recency_factor
          + 0.10 * norm_ab_win_rate

All dimensions except ``recency_factor`` are normalised to [0, 1] using
min-max scaling within the prompt family (max-anchored, min = 0).
``recency_factor`` is a continuous value computed directly from
``updated_at`` age using a 365-day decay window.

Tie-break (stable, deterministic)
-----------------------------------
    1. score desc
    2. version desc         (higher version preferred)
    3. updated_at desc      (more recently edited preferred)
    4. post id desc         (deterministic row-ordering)

Scope rules (enforced at the SQL query level)
----------------------------------------------
Public scope (workspace=None):
  - Only public forks (Post.workspace_id IS NULL, status=published).
  - Only public benchmark suites (BenchmarkSuite.workspace_id IS NULL).
  - Only public A/B experiments (ABExperiment.workspace_id IS NULL).

Workspace scope (workspace=<ws>):
  - Public forks  +  forks belonging to the SAME workspace.
  - Public suites  +  same-workspace suites.
  - Public A/B experiments  +  same-workspace experiments.
  - Items from OTHER workspaces are NEVER included.

Query pattern (bounded, no N+1)
---------------------------------
1. Fork family          — 1 query  (ContentLink JOIN Post, scoped)
2. Vote counts          — 1 aggregation query
3. Benchmark avg        — 1 aggregation query  (JOIN runs + results + suites)
4. AB experiments       — 1 query  (scoped)
5. AB experiment runs   — 1 query  (batch by experiment_id IN)
6. Run avg scores       — 1 aggregation query  (batch by run_id IN)

Total: 6 bounded SQL queries regardless of family size.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from backend.extensions import db
from backend.models.ab_experiment import (
    ABExperiment,
    ABExperimentRun,
    ABExperimentStatus,
)
from backend.models.benchmark import (
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.content_link import ContentLink
from backend.models.ontology import ContentOntology
from backend.models.post import Post, PostStatus
from backend.models.vote import Vote

# ── Scoring weights ────────────────────────────────────────────────────────────

_W_BENCHMARK: float = 0.40
_W_RATING: float = 0.25
_W_EXECUTION: float = 0.15
_W_RECENCY: float = 0.10
_W_AB: float = 0.10

# Days of age at which recency_factor reaches 0.
_RECENCY_DECAY_DAYS: float = 365.0

# Hard cap on family size to prevent unbounded memory usage.
_MAX_FAMILY_SIZE: int = 50


# ── DTOs ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForkScoreBreakdown:
    """Signal values and per-dimension weighted contributions for one fork."""

    # Raw inputs
    benchmark_raw: float | None    # avg numeric score; None when no qualifying runs
    rating_raw: int                # total vote count
    execution_raw: int             # Post.view_count (execution proxy)
    recency_factor: float          # continuous 0–1 derived from updated_at age
    ab_win_rate: float | None      # fraction of completed A/B experiments won; None if none

    # Weighted contributions (weight × normalised value)
    benchmark_contrib: float
    rating_contrib: float
    execution_contrib: float
    recency_contrib: float
    ab_contrib: float

    # Final composite
    score: float


@dataclass(frozen=True)
class ForkRecommendation:
    """A ranked fork with its composite score and breakdown."""

    post_id: int
    title: str
    slug: str
    kind: str
    scope: str                     # 'public' | 'workspace'
    version: int
    workspace_id: int | None
    breakdown: ForkScoreBreakdown
    score: float


# ── Public API ─────────────────────────────────────────────────────────────────


def recommend(
    user: object,
    base_prompt: Post,
    workspace: object | None = None,
    *,
    ontology_node: object | None = None,
) -> list[ForkRecommendation]:
    """Return a ranked list of fork recommendations for *base_prompt*.

    When *ontology_node* is given only forks that are mapped to that node
    (or any of its public descendants) via ``content_ontology`` are returned.
    Scope of the mapping lookup mirrors the *workspace* parameter.

    Returns an empty list when the user is unauthenticated or no forks exist.
    Callers are responsible for workspace membership checks before passing
    a non-None *workspace*.
    """
    if user is None:
        return []

    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    # ── Query 1: fork family ──────────────────────────────────────────────
    forks = compute_family(base_prompt, workspace)
    if not forks:
        return []

    # ── Optional: filter by ontology node ────────────────────────────────
    if ontology_node is not None:
        forks = _filter_forks_by_ontology(forks, ontology_node, ws_id)
        if not forks:
            return []

    fork_ids = [f.id for f in forks]
    forks_by_id: dict[int, Post] = {f.id: f for f in forks}

    # ── Queries 2–6: raw signal data ──────────────────────────────────────
    vote_map = _load_vote_counts(fork_ids)
    bench_map = _load_benchmark_avgs(fork_ids, ws_id)
    ab_map = _load_ab_win_rates(fork_ids, ws_id)

    # ── Pre-compute normalisation denominators ────────────────────────────
    raw_bench_vals = [v for v in bench_map.values() if v is not None]
    max_bench = max(raw_bench_vals, default=0.0)
    max_votes = max((vote_map.get(fid, 0) for fid in fork_ids), default=0)
    max_exec = max((forks_by_id[fid].view_count for fid in fork_ids), default=0)
    raw_ab_vals = [v for v in ab_map.values() if v is not None]
    max_ab = max(raw_ab_vals, default=0.0)

    now_utc = datetime.now(UTC)

    # ── Build recommendations ─────────────────────────────────────────────
    recs: list[ForkRecommendation] = []
    for fork in forks:
        fid = fork.id
        bench_raw = bench_map.get(fid)
        vote_raw = vote_map.get(fid, 0)
        exec_raw = fork.view_count
        ab_raw = ab_map.get(fid)

        # Recency factor — continuous, pre-normalised to [0, 1].
        updated = _ensure_tz(fork.updated_at)
        age_days = max(0, (now_utc - updated).days)
        recency = max(0.0, 1.0 - age_days / _RECENCY_DECAY_DAYS)

        # Normalised dimensions (max-anchored, floor = 0).
        norm_bench = (bench_raw / max_bench) if (bench_raw is not None and max_bench > 0) else 0.0
        norm_votes = (vote_raw / max_votes) if max_votes > 0 else 0.0
        norm_exec = (exec_raw / max_exec) if max_exec > 0 else 0.0
        norm_ab = (ab_raw / max_ab) if (ab_raw is not None and max_ab > 0) else 0.0

        score = (
            _W_BENCHMARK * norm_bench
            + _W_RATING * norm_votes
            + _W_EXECUTION * norm_exec
            + _W_RECENCY * recency
            + _W_AB * norm_ab
        )

        breakdown = ForkScoreBreakdown(
            benchmark_raw=bench_raw,
            rating_raw=vote_raw,
            execution_raw=exec_raw,
            recency_factor=round(recency, 4),
            ab_win_rate=ab_raw,
            benchmark_contrib=round(_W_BENCHMARK * norm_bench, 4),
            rating_contrib=round(_W_RATING * norm_votes, 4),
            execution_contrib=round(_W_EXECUTION * norm_exec, 4),
            recency_contrib=round(_W_RECENCY * recency, 4),
            ab_contrib=round(_W_AB * norm_ab, 4),
            score=round(score, 6),
        )

        recs.append(
            ForkRecommendation(
                post_id=fid,
                title=fork.title,
                slug=fork.slug,
                kind=fork.kind,
                scope="workspace" if fork.workspace_id is not None else "public",
                version=fork.version,
                workspace_id=fork.workspace_id,
                breakdown=breakdown,
                score=round(score, 6),
            )
        )

    # ── Sort (stable, deterministic) ──────────────────────────────────────
    recs.sort(
        key=lambda r: (
            r.score,
            r.version,
            _ensure_tz(forks_by_id[r.post_id].updated_at).timestamp(),
            r.post_id,
        ),
        reverse=True,
    )
    return recs


def compute_family(
    base_prompt: Post,
    workspace: object | None = None,
) -> list[Post]:
    """Return all scope-visible published forks of *base_prompt*.

    Forks are posts with a ``derived_from`` ContentLink pointing at
    *base_prompt*, the base prompt itself is excluded.

    Results capped at :data:`_MAX_FAMILY_SIZE` (ordered by id).
    """
    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    scope_clause = (
        Post.workspace_id.is_(None)
        if ws_id is None
        else or_(Post.workspace_id.is_(None), Post.workspace_id == ws_id)
    )

    fork_rows = db.session.scalars(
        select(Post)
        .join(ContentLink, ContentLink.from_post_id == Post.id)
        .where(
            ContentLink.to_post_id == base_prompt.id,
            ContentLink.link_type == "derived_from",
            Post.status == PostStatus.published,
            Post.id != base_prompt.id,          # exclude base prompt
            scope_clause,
        )
        .order_by(Post.id)
        .limit(_MAX_FAMILY_SIZE)
    ).all()

    return list(fork_rows)


def build_breakdown(rec: ForkRecommendation) -> ForkScoreBreakdown:
    """Return the breakdown for an already-scored recommendation (passthrough)."""
    return rec.breakdown


# ── Internal helpers ───────────────────────────────────────────────────────────


def _load_vote_counts(fork_ids: list[int]) -> dict[int, int]:
    """Return {post_id: vote_count} for all *fork_ids* (single aggregation query)."""
    if not fork_ids:
        return {}
    rows = db.session.execute(
        select(Vote.target_id, func.count(Vote.id).label("cnt"))
        .where(Vote.target_type == "post", Vote.target_id.in_(fork_ids))
        .group_by(Vote.target_id)
    ).all()
    return {row.target_id: row.cnt for row in rows}


def _load_benchmark_avgs(
    fork_ids: list[int],
    ws_id: int | None,
) -> dict[int, float | None]:
    """Return {post_id: avg_score} for forks that have completed benchmark runs.

    Only scores from qualifying suites (scoped) are included.
    Posts with no qualifying results are omitted from the returned dict
    (callers default to None).
    """
    if not fork_ids:
        return {}

    suite_scope = (
        BenchmarkSuite.workspace_id.is_(None)
        if ws_id is None
        else or_(BenchmarkSuite.workspace_id.is_(None), BenchmarkSuite.workspace_id == ws_id)
    )

    rows = db.session.execute(
        select(
            BenchmarkRun.prompt_post_id,
            func.avg(BenchmarkRunResult.score_numeric).label("avg_score"),
        )
        .join(BenchmarkRunResult, BenchmarkRunResult.run_id == BenchmarkRun.id)
        .join(BenchmarkSuite, BenchmarkSuite.id == BenchmarkRun.suite_id)
        .where(
            BenchmarkRun.prompt_post_id.in_(fork_ids),
            BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            BenchmarkRunResult.score_numeric.is_not(None),
            suite_scope,
        )
        .group_by(BenchmarkRun.prompt_post_id)
    ).all()

    return {row.prompt_post_id: float(row.avg_score) for row in rows if row.avg_score is not None}


def _load_ab_win_rates(
    fork_ids: list[int],
    ws_id: int | None,
) -> dict[int, float | None]:
    """Return {post_id: win_rate} for forks involved in completed A/B experiments.

    ``win_rate`` = wins / total_completed_experiments_for_fork.
    A fork "wins" when its variant had a strictly higher avg score than the
    other variant in a completed experiment.

    Posts with no completed experiments are omitted (caller defaults to None).
    """
    if not fork_ids:
        return {}

    exp_scope = (
        ABExperiment.workspace_id.is_(None)
        if ws_id is None
        else or_(ABExperiment.workspace_id.is_(None), ABExperiment.workspace_id == ws_id)
    )

    # ── Query 4: completed experiments involving our forks ────────────────
    exps = db.session.scalars(
        select(ABExperiment).where(
            ABExperiment.status == ABExperimentStatus.completed.value,
            or_(
                ABExperiment.variant_a_prompt_post_id.in_(fork_ids),
                ABExperiment.variant_b_prompt_post_id.in_(fork_ids),
            ),
            exp_scope,
        )
    ).all()

    if not exps:
        return {}

    exp_ids = [e.id for e in exps]

    # ── Query 5: run-id pairs for those experiments ───────────────────────
    exp_runs = db.session.scalars(
        select(ABExperimentRun).where(ABExperimentRun.experiment_id.in_(exp_ids))
    ).all()

    exp_run_map: dict[int, ABExperimentRun] = {er.experiment_id: er for er in exp_runs}

    all_run_ids = {er.run_a_id for er in exp_runs} | {er.run_b_id for er in exp_runs}
    if not all_run_ids:
        return {}

    # ── Query 6: avg score per run ────────────────────────────────────────
    run_avg_rows = db.session.execute(
        select(
            BenchmarkRunResult.run_id,
            func.avg(BenchmarkRunResult.score_numeric).label("avg_score"),
        )
        .where(
            BenchmarkRunResult.run_id.in_(all_run_ids),
            BenchmarkRunResult.score_numeric.is_not(None),
        )
        .group_by(BenchmarkRunResult.run_id)
    ).all()

    run_avg: dict[int, float] = {
        row.run_id: float(row.avg_score) for row in run_avg_rows if row.avg_score is not None
    }

    # ── Tally wins per fork ───────────────────────────────────────────────
    wins: dict[int, int] = {}
    totals: dict[int, int] = {}

    for exp in exps:
        er = exp_run_map.get(exp.id)
        if er is None:
            continue

        avg_a = run_avg.get(er.run_a_id)
        avg_b = run_avg.get(er.run_b_id)

        # Skip experiment if either side has no scored results.
        if avg_a is None or avg_b is None:
            continue

        fork_a = exp.variant_a_prompt_post_id
        fork_b = exp.variant_b_prompt_post_id

        for fid in (fork_a, fork_b):
            if fid in fork_ids:
                totals[fid] = totals.get(fid, 0) + 1

        # A wins?
        if fork_a in fork_ids:
            if avg_a > avg_b:
                wins[fork_a] = wins.get(fork_a, 0) + 1

        # B wins?
        if fork_b in fork_ids:
            if avg_b > avg_a:
                wins[fork_b] = wins.get(fork_b, 0) + 1

    result: dict[int, float | None] = {}
    for fid in fork_ids:
        total = totals.get(fid, 0)
        if total == 0:
            result[fid] = None
        else:
            result[fid] = wins.get(fid, 0) / total

    # Remove None entries so callers can distinguish "no experiments" from 0%.
    return {k: v for k, v in result.items() if v is not None}


def _filter_forks_by_ontology(
    forks: list[Post],
    node: object,
    ws_id: int | None,
) -> list[Post]:
    """Return only forks mapped to *node* or its public descendants.

    Uses a single SQL query against ``content_ontology``.  Scope rules:
    - Public scope (ws_id is None): ``content_ontology.workspace_id IS NULL``.
    - Workspace scope: public rows OR ``workspace_id = ws_id``.
    """
    from backend.services.ontology_service import (
        get_all_descendant_ids,  # noqa: PLC0415
    )

    node_ids = get_all_descendant_ids(node.id, public_only=True)  # type: ignore[union-attr]
    if not node_ids:
        return []

    fork_ids = [f.id for f in forks]

    mapping_scope = (
        ContentOntology.workspace_id.is_(None)
        if ws_id is None
        else or_(
            ContentOntology.workspace_id.is_(None),
            ContentOntology.workspace_id == ws_id,
        )
    )

    mapped_post_ids: set[int] = set(
        db.session.scalars(
            select(ContentOntology.post_id).where(
                ContentOntology.post_id.in_(fork_ids),
                ContentOntology.ontology_node_id.in_(node_ids),
                mapping_scope,
            )
        ).all()
    )

    return [f for f in forks if f.id in mapped_post_ids]


def _ensure_tz(dt: datetime) -> datetime:
    """Return *dt* as a timezone-aware datetime (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
