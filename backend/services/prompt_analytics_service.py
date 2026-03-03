"""Prompt Evolution Analytics service — deterministic, scope-isolated aggregations.

Design
------
Four public functions aggregate the four analytics panels shown on the
``/prompts/<slug>/analytics`` page:

- ``get_version_timeline``  — ordered list of accepted revisions with cumulative
                              vote counts and AI-attribution flags.
- ``get_rating_trend``      — per-version vote counts + delta from previous version.
- ``get_fork_tree``         — posts that list this prompt as ``derived_from`` in the
                              ContentLink graph, scope-filtered.
- ``get_execution_stats``   — total views, recent views, and unique readers.

Scope isolation
---------------
- ``workspace_id=None``  → public context.
  Fork tree queries include only published posts in the public layer
  (Post.workspace_id IS NULL).
- ``workspace_id=ws_id`` → workspace context.
  Fork tree queries include published posts that are public OR belong to ws_id.
  Items from a *different* workspace are excluded at the SQL level.

Votes, version history, and execution stats are scoped to the prompt post
itself and therefore carry no cross-workspace risk.

Query pattern (bounded, no N+1)
---------------------------------
get_version_timeline / get_rating_trend
  1  PostVersion  WHERE post_id=X ORDER BY version_number
  2  Revision     WHERE id IN (revision_ids from step 1)
  3  User         WHERE id IN (author_ids collected from revisions)
  4  PostReleaseNote  WHERE post_id=X ORDER BY version_number
  5  Vote timestamps  SELECT created_at WHERE target_type='post' AND target_id=X

get_fork_tree
  6  ContentLink  +JOIN Post  WHERE to_post_id=X AND link_type='derived_from'
                              + scope WHERE clause
  7  Vote counts  SELECT target_id, COUNT(*) … GROUP BY target_id

get_execution_stats
  8  AnalyticsEvent COUNT  WHERE post_id=X AND event_type='post_view' AND occurred_at > now-30d
  9  UserPostRead   COUNT  WHERE post_id=X

Total: 7–9 bounded queries regardless of data volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from backend.extensions import db
from backend.models.ab_experiment import (
    ABExperiment,
    ABExperimentRun,
    ABExperimentStatus,
)
from backend.models.analytics import AnalyticsEvent
from backend.models.benchmark import (
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
)
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.post_release_note import PostReleaseNote
from backend.models.post_version import PostVersion
from backend.models.revision import Revision
from backend.models.user import User
from backend.models.user_post_read import UserPostRead
from backend.models.vote import Vote

# ── Result dataclasses ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VersionEntry:
    """A single entry in the prompt version timeline."""

    version_number: int
    created_at: datetime
    summary: str  # release-note text or revision summary
    author_display: str  # display_name or username of revision author
    vote_count_cumulative: int  # total votes that existed when this version was created
    is_ai_generated: bool  # True when revision.source_metadata_json records AI origin


@dataclass(frozen=True)
class RatingSnapshot:
    """Vote count and delta for one version."""

    version_number: int
    vote_count: int  # cumulative votes at this version
    delta: int  # votes gained since the previous version (0 for v1)


@dataclass(frozen=True)
class ForkEntry:
    """A post that derives from the current prompt via a derived_from ContentLink."""

    post_id: int
    title: str
    slug: str
    kind: str
    scope: str  # 'public' | 'workspace'
    vote_count: int
    created_at: datetime


@dataclass(frozen=True)
class ExecutionStats:
    """Aggregate view / reader metrics for a prompt post."""

    total_views: int  # Post.view_count (denormalised counter)
    views_last_30_days: int  # AnalyticsEvent rows in the last 30 days
    unique_readers: int  # UserPostRead rows (one per authenticated user)


# ── Public interface ──────────────────────────────────────────────────────────


def get_version_timeline(
    prompt_post: Post,
    workspace_id: int | None = None,  # noqa: ARG001  (reserved for future scope use)
) -> list[VersionEntry]:
    """Return ordered version history for *prompt_post*.

    Entries are ordered by ``version_number`` ascending (oldest first).
    Includes the initial v1 implicit creation (represented by the first
    PostVersion row, if present).

    Votes are cumulated per-version by comparing vote timestamps to the
    PostVersion creation timestamp.
    """
    # ── 1. PostVersion records ────────────────────────────────────────────────
    versions: list[PostVersion] = (
        db.session.execute(
            select(PostVersion)
            .where(PostVersion.post_id == prompt_post.id)
            .order_by(PostVersion.version_number)
        )
        .scalars()
        .all()
    )

    if not versions:
        # No explicit version history yet — post still at v1 with no accepted revisions.
        return []

    # ── 2. Revisions for each version (via PostVersion.revision_id) ───────────
    revision_ids: list[int] = [
        v.revision_id for v in versions if v.revision_id is not None
    ]
    revision_map: dict[int, Revision] = {}
    if revision_ids:
        rows = db.session.execute(
            select(Revision).where(Revision.id.in_(revision_ids))
        ).scalars()
        revision_map = {r.id: r for r in rows}

    # ── 3. Authors ────────────────────────────────────────────────────────────
    author_ids: set[int] = set()
    for rev in revision_map.values():
        author_ids.add(rev.author_id)
    author_ids.add(prompt_post.author_id)  # v1 author fallback

    user_map: dict[int, User] = {}
    if author_ids:
        rows = db.session.execute(select(User).where(User.id.in_(author_ids))).scalars()
        user_map = {u.id: u for u in rows}

    # ── 4. Release notes (preferred human-readable summary) ───────────────────
    release_notes: list[PostReleaseNote] = (
        db.session.execute(
            select(PostReleaseNote)
            .where(PostReleaseNote.post_id == prompt_post.id)
            .order_by(PostReleaseNote.version_number)
        )
        .scalars()
        .all()
    )
    release_note_map: dict[int, str] = {
        rn.version_number: rn.summary for rn in release_notes
    }

    # ── 5. Vote timestamps (all votes for this post, ordered ascending) ───────
    vote_times: list[datetime] = list(
        db.session.execute(
            select(Vote.created_at)
            .where(Vote.target_type == "post", Vote.target_id == prompt_post.id)
            .order_by(Vote.created_at)
        ).scalars()
    )

    # ── Assemble VersionEntry list ─────────────────────────────────────────────
    result: list[VersionEntry] = []
    for pv in versions:
        # Cumulative votes as of version creation time
        cum_votes = sum(1 for vt in vote_times if vt <= pv.created_at)

        # Summary: prefer release note, then revision summary, then fallback
        summary = release_note_map.get(pv.version_number, "")
        if not summary and pv.revision_id and pv.revision_id in revision_map:
            summary = revision_map[pv.revision_id].summary
        if not summary:
            summary = "Initial version" if pv.version_number == 1 else "Updated"

        # Author: from revision if available, else post author for v1
        author_id_for_version = (
            revision_map[pv.revision_id].author_id
            if (pv.revision_id and pv.revision_id in revision_map)
            else prompt_post.author_id
        )
        user = user_map.get(author_id_for_version)
        author_display = (user.display_name or user.username) if user else "Unknown"

        # AI attribution
        is_ai = False
        if pv.revision_id and pv.revision_id in revision_map:
            meta = revision_map[pv.revision_id].source_metadata_json
            is_ai = isinstance(meta, dict) and meta.get("source") == "ai_suggestion"

        result.append(
            VersionEntry(
                version_number=pv.version_number,
                created_at=pv.created_at,
                summary=summary,
                author_display=author_display,
                vote_count_cumulative=cum_votes,
                is_ai_generated=is_ai,
            )
        )

    return result


def get_rating_trend(
    prompt_post: Post,
    workspace_id: int | None = None,
) -> list[RatingSnapshot]:
    """Return per-version vote counts and deltas.

    Reuses the data gathered by ``get_version_timeline`` but presents it as
    a flat list of (version_number, vote_count, delta) triples suitable for
    rendering as a table or sparkline.

    Ordered ascending by version_number.
    """
    timeline = get_version_timeline(prompt_post, workspace_id=workspace_id)
    if not timeline:
        return []

    snapshots: list[RatingSnapshot] = []
    for i, entry in enumerate(timeline):
        prev_count = timeline[i - 1].vote_count_cumulative if i > 0 else 0
        delta = 0 if i == 0 else entry.vote_count_cumulative - prev_count
        snapshots.append(
            RatingSnapshot(
                version_number=entry.version_number,
                vote_count=entry.vote_count_cumulative,
                delta=delta,
            )
        )
    return snapshots


def get_fork_tree(
    prompt_post: Post,
    workspace_id: int | None = None,
) -> list[ForkEntry]:
    """Return posts that link to *prompt_post* with link_type='derived_from'.

    Scope isolation:
    - Public (workspace_id=None): only forks that are publicly published
      (Post.workspace_id IS NULL, status=published).
    - Workspace (workspace_id=ws_id): publicly published forks plus forks in
      the same workspace.  Forks from other workspaces are excluded.

    Results are ordered by ``created_at`` descending (newest forks first).
    """
    # ── 6. Content links pointing to this post with derived_from ─────────────
    scope_clause = (
        Post.workspace_id.is_(None)
        if workspace_id is None
        else or_(Post.workspace_id.is_(None), Post.workspace_id == workspace_id)
    )

    fork_rows = (
        db.session.execute(
            select(Post)
            .join(ContentLink, ContentLink.from_post_id == Post.id)
            .where(
                ContentLink.to_post_id == prompt_post.id,
                ContentLink.link_type == "derived_from",
                Post.status == PostStatus.published,
                scope_clause,
            )
            .order_by(Post.created_at.desc())
        )
        .scalars()
        .all()
    )

    if not fork_rows:
        return []

    fork_ids = [p.id for p in fork_rows]

    # ── 7. Vote counts for forks ──────────────────────────────────────────────
    vote_rows = db.session.execute(
        select(Vote.target_id, func.count(Vote.id).label("cnt"))
        .where(Vote.target_type == "post", Vote.target_id.in_(fork_ids))
        .group_by(Vote.target_id)
    ).all()
    vote_map: dict[int, int] = {row.target_id: row.cnt for row in vote_rows}

    return [
        ForkEntry(
            post_id=p.id,
            title=p.title,
            slug=p.slug,
            kind=p.kind,
            scope="workspace" if p.workspace_id is not None else "public",
            vote_count=vote_map.get(p.id, 0),
            created_at=p.created_at,
        )
        for p in fork_rows
    ]


def get_execution_stats(
    prompt_post: Post,
    workspace_id: int | None = None,  # noqa: ARG001
) -> ExecutionStats:
    """Return view and reader metrics for *prompt_post*.

    Uses:
    - ``Post.view_count`` for the denormalised total.
    - ``AnalyticsEvent`` (event_type='post_view') for recent-30-day count.
    - ``UserPostRead`` for unique authenticated-reader count.

    These metrics are scoped to the post itself and carry no cross-workspace
    risk (no workspace column on Vote or AnalyticsEvent).
    """
    # ── 8. Recent views from AnalyticsEvent ──────────────────────────────────
    cutoff = datetime.now(UTC) - timedelta(days=30)
    views_recent: int = (
        db.session.execute(
            select(func.count(AnalyticsEvent.id)).where(
                AnalyticsEvent.post_id == prompt_post.id,
                AnalyticsEvent.event_type == "post_view",
                AnalyticsEvent.occurred_at >= cutoff,
            )
        ).scalar()
        or 0
    )

    # ── 9. Unique authenticated readers ───────────────────────────────────────
    unique_readers: int = (
        db.session.execute(
            select(func.count(UserPostRead.id)).where(
                UserPostRead.post_id == prompt_post.id
            )
        ).scalar()
        or 0
    )

    return ExecutionStats(
        total_views=prompt_post.view_count,
        views_last_30_days=views_recent,
        unique_readers=unique_readers,
    )


# ── Prompt Evolution Analytics — extended per-version and fork views ──────────


@dataclass(frozen=True)
class VersionMetrics:
    """Aggregated metrics for one prompt version."""

    version: int
    updated_at: datetime | None  # PostVersion.created_at; None if no snapshot
    benchmark_avg: float | None  # avg numeric score across completed runs
    execution_count: int  # completed benchmark runs for this version
    rating_count: int  # cumulative votes at the end of this version
    rating_delta: int  # votes gained vs previous version (0 for v1)
    ab_wins: int  # number of completed A/B experiments won
    ab_losses: int  # number of completed A/B experiments lost
    delta_benchmark: (
        float | None
    )  # benchmark_avg − previous version's avg (None for v1)


@dataclass(frozen=True)
class ForkComparisonEntry:
    """One row in the fork comparison ranking table."""

    post_id: int
    title: str
    slug: str
    kind: str
    scope: str  # 'public' | 'workspace'
    is_origin: bool
    benchmark_avg: float | None
    vote_count: int
    ab_win_rate: float | None
    composite_score: float  # 0.60·norm_bench + 0.30·norm_votes + 0.10·norm_ab


@dataclass(frozen=True)
class ForkComparison:
    """Summary comparing the origin prompt against its forks."""

    origin_score: float | None  # benchmark_avg of the origin prompt
    best_fork_score: float | None  # best benchmark_avg among forks
    fork_count: int
    entries: list[ForkComparisonEntry]  # ranked desc by composite_score


def build_version_metrics(
    prompt: Post,
    workspace: object | None = None,
) -> list[VersionMetrics]:
    """Return per-version aggregated metrics for *prompt*.

    Gathers benchmark scores, vote deltas, and A/B win/loss counts grouped
    by version number.  Results are ordered by version ascending.

    Scope rules (enforced at SQL level)
    ------------------------------------
    Public (workspace=None): only benchmark runs with workspace_id IS NULL.
    Workspace: public + same-workspace runs.

    Query plan (6 queries max, bounded)
    ------------------------------------
    Q1  PostVersion rows (timestamps for per-version vote delta windows).
    Q2  Benchmark aggregation: avg score + run count grouped by prompt_version.
    Q3  Vote timestamps ordered ascending (for per-version delta).
    Q4  ABExperiment rows (completed, scoped, involving this prompt).
    Q5  ABExperimentRun rows for those experiments (batch).
    Q6  BenchmarkRunResult avg per run (batch, for A/B win determination).
    """
    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]

    # ── Q1: PostVersion rows ──────────────────────────────────────────────
    pv_rows: list[PostVersion] = list(
        db.session.scalars(
            select(PostVersion)
            .where(PostVersion.post_id == prompt.id)
            .order_by(PostVersion.version_number)
        ).all()
    )
    pv_by_version: dict[int, PostVersion] = {pv.version_number: pv for pv in pv_rows}

    # ── Q2: Benchmark aggregation by prompt_version ───────────────────────
    run_scope = (
        BenchmarkRun.workspace_id.is_(None)
        if ws_id is None
        else or_(
            BenchmarkRun.workspace_id.is_(None),
            BenchmarkRun.workspace_id == ws_id,
        )
    )
    bench_rows = db.session.execute(
        select(
            BenchmarkRun.prompt_version,
            func.avg(BenchmarkRunResult.score_numeric).label("avg_score"),
            func.count(BenchmarkRun.id.distinct()).label("run_count"),
        )
        .join(BenchmarkRunResult, BenchmarkRunResult.run_id == BenchmarkRun.id)
        .where(
            BenchmarkRun.prompt_post_id == prompt.id,
            BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            BenchmarkRunResult.score_numeric.is_not(None),
            run_scope,
        )
        .group_by(BenchmarkRun.prompt_version)
    ).all()

    bench_by_version: dict[int, tuple[float | None, int]] = {
        row.prompt_version: (
            float(row.avg_score) if row.avg_score is not None else None,
            row.run_count,
        )
        for row in bench_rows
    }

    # ── Q3: Vote timestamps (for per-version delta) ───────────────────────
    vote_times: list[datetime] = list(
        db.session.scalars(
            select(Vote.created_at)
            .where(Vote.target_type == "post", Vote.target_id == prompt.id)
            .order_by(Vote.created_at)
        ).all()
    )

    # ── Q4: Completed A/B experiments involving this prompt ───────────────
    exp_scope = (
        ABExperiment.workspace_id.is_(None)
        if ws_id is None
        else or_(
            ABExperiment.workspace_id.is_(None),
            ABExperiment.workspace_id == ws_id,
        )
    )
    exps: list[ABExperiment] = list(
        db.session.scalars(
            select(ABExperiment).where(
                ABExperiment.status == ABExperimentStatus.completed.value,
                or_(
                    ABExperiment.variant_a_prompt_post_id == prompt.id,
                    ABExperiment.variant_b_prompt_post_id == prompt.id,
                ),
                exp_scope,
            )
        ).all()
    )

    ab_wins_by_version: dict[int, int] = {}
    ab_losses_by_version: dict[int, int] = {}

    if exps:
        exp_ids = [e.id for e in exps]

        # ── Q5: ABExperimentRun rows ──────────────────────────────────────
        exp_runs: list[ABExperimentRun] = list(
            db.session.scalars(
                select(ABExperimentRun).where(
                    ABExperimentRun.experiment_id.in_(exp_ids)
                )
            ).all()
        )

        all_run_ids: set[int] = {er.run_a_id for er in exp_runs} | {
            er.run_b_id for er in exp_runs
        }

        # ── Q6: BenchmarkRunResult avg per run ────────────────────────────
        run_avg_map: dict[int, float] = {}
        if all_run_ids:
            run_avg_rows = db.session.execute(
                select(
                    BenchmarkRunResult.run_id,
                    func.avg(BenchmarkRunResult.score_numeric).label("avg"),
                )
                .where(
                    BenchmarkRunResult.run_id.in_(all_run_ids),
                    BenchmarkRunResult.score_numeric.is_not(None),
                )
                .group_by(BenchmarkRunResult.run_id)
            ).all()
            run_avg_map = {
                r.run_id: float(r.avg) for r in run_avg_rows if r.avg is not None
            }

        exp_run_map: dict[int, ABExperimentRun] = {
            er.experiment_id: er for er in exp_runs
        }

        for exp in exps:
            er = exp_run_map.get(exp.id)
            if er is None:
                continue
            avg_a = run_avg_map.get(er.run_a_id)
            avg_b = run_avg_map.get(er.run_b_id)
            if avg_a is None or avg_b is None:
                continue

            if exp.variant_a_prompt_post_id == prompt.id:
                our_version = exp.variant_a_version
                won = avg_a > avg_b
            else:
                our_version = exp.variant_b_version
                won = avg_b > avg_a

            if won:
                ab_wins_by_version[our_version] = (
                    ab_wins_by_version.get(our_version, 0) + 1
                )
            else:
                ab_losses_by_version[our_version] = (
                    ab_losses_by_version.get(our_version, 0) + 1
                )

    # ── Assemble version list from PostVersion + benchmark runs ───────────
    all_versions: list[int] = sorted(
        set(pv_by_version.keys()) | set(bench_by_version.keys())
    )

    if not all_versions:
        return []

    # ── Build result ──────────────────────────────────────────────────────
    result: list[VersionMetrics] = []
    prev_bench: float | None = None
    prev_votes: int = 0

    for v in all_versions:
        bench_avg, run_count = bench_by_version.get(v, (None, 0))
        pv = pv_by_version.get(v)
        updated_at = pv.created_at if pv is not None else None

        # Cumulative vote count at this version's creation timestamp
        if pv is not None:
            cum_votes = sum(1 for vt in vote_times if vt <= pv.created_at)
        else:
            cum_votes = len(vote_times)

        rating_delta = cum_votes - prev_votes

        delta_bench: float | None = None
        if bench_avg is not None and prev_bench is not None:
            delta_bench = round(bench_avg - prev_bench, 4)

        result.append(
            VersionMetrics(
                version=v,
                updated_at=updated_at,
                benchmark_avg=round(bench_avg, 4) if bench_avg is not None else None,
                execution_count=run_count,
                rating_count=cum_votes,
                rating_delta=rating_delta,
                ab_wins=ab_wins_by_version.get(v, 0),
                ab_losses=ab_losses_by_version.get(v, 0),
                delta_benchmark=delta_bench,
            )
        )

        prev_bench = bench_avg if bench_avg is not None else prev_bench
        prev_votes = cum_votes

    return result


def compute_trend_label(metrics: list[VersionMetrics]) -> str:
    """Return a trend label based on the last two versions' benchmark averages.

    Returns one of: ``'improving'``, ``'regressing'``, ``'stable'``,
    ``'insufficient_data'``.
    Pure Python — no SQL queries.
    """
    benchmarked = [m for m in metrics if m.benchmark_avg is not None]
    if len(benchmarked) < 2:
        return "insufficient_data"
    latest = benchmarked[-1].benchmark_avg
    prev = benchmarked[-2].benchmark_avg
    # Both are non-None by construction from the filter above.
    if latest is None or prev is None:  # pragma: no cover  # type narrowing
        return "insufficient_data"
    if latest > prev:
        return "improving"
    if latest < prev:
        return "regressing"
    return "stable"


def build_fork_comparison(
    prompt: Post,
    workspace: object | None = None,
) -> ForkComparison:
    """Rank the origin prompt against its forks by composite score.

    Scope rules (enforced at SQL level)
    ------------------------------------
    Public (workspace=None): only published public forks; public bench runs.
    Workspace: public + same-workspace forks; public + same-workspace runs.

    Query plan (6 queries max, bounded)
    ------------------------------------
    Q1  ContentLink + Post → fork family (scoped, LIMIT 50).
    Q2  BenchmarkRun + BenchmarkRunResult → avg score per post_id (scoped).
    Q3  Vote.target_id / COUNT → vote counts per post_id.
    Q4  ABExperiment rows (completed, scoped, involving any of these posts).
    Q5  ABExperimentRun rows (batch by experiment_id IN).
    Q6  BenchmarkRunResult avg per run_id (batch, for A/B win determination).

    Composite score = 0.60 · norm_bench + 0.30 · norm_votes + 0.10 · norm_ab
    (max-anchored min-max within this family).

    Tie-break: composite_score DESC → post_id DESC.
    """
    ws_id: int | None = workspace.id if workspace is not None else None  # type: ignore[union-attr]
    _MAX_FORKS = 50

    # ── Q1: Fork family ───────────────────────────────────────────────────
    post_scope = (
        Post.workspace_id.is_(None)
        if ws_id is None
        else or_(Post.workspace_id.is_(None), Post.workspace_id == ws_id)
    )
    fork_posts: list[Post] = list(
        db.session.scalars(
            select(Post)
            .join(ContentLink, ContentLink.from_post_id == Post.id)
            .where(
                ContentLink.to_post_id == prompt.id,
                ContentLink.link_type == "derived_from",
                Post.status == PostStatus.published,
                post_scope,
            )
            .order_by(Post.id)
            .limit(_MAX_FORKS)
        ).all()
    )

    all_post_ids: list[int] = [prompt.id] + [f.id for f in fork_posts]

    # ── Q2: Benchmark avg per post (scoped) ───────────────────────────────
    run_scope = (
        BenchmarkRun.workspace_id.is_(None)
        if ws_id is None
        else or_(
            BenchmarkRun.workspace_id.is_(None),
            BenchmarkRun.workspace_id == ws_id,
        )
    )
    bench_rows2 = db.session.execute(
        select(
            BenchmarkRun.prompt_post_id,
            func.avg(BenchmarkRunResult.score_numeric).label("avg_score"),
        )
        .join(BenchmarkRunResult, BenchmarkRunResult.run_id == BenchmarkRun.id)
        .where(
            BenchmarkRun.prompt_post_id.in_(all_post_ids),
            BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            BenchmarkRunResult.score_numeric.is_not(None),
            run_scope,
        )
        .group_by(BenchmarkRun.prompt_post_id)
    ).all()
    bench_avg_map: dict[int, float] = {
        r.prompt_post_id: float(r.avg_score)
        for r in bench_rows2
        if r.avg_score is not None
    }

    # ── Q3: Vote counts per post ──────────────────────────────────────────
    vote_rows2 = db.session.execute(
        select(Vote.target_id, func.count(Vote.id).label("cnt"))
        .where(Vote.target_type == "post", Vote.target_id.in_(all_post_ids))
        .group_by(Vote.target_id)
    ).all()
    vote_map2: dict[int, int] = {row.target_id: row.cnt for row in vote_rows2}

    # ── Q4: Completed A/B experiments involving any of these posts ────────
    exp_scope2 = (
        ABExperiment.workspace_id.is_(None)
        if ws_id is None
        else or_(
            ABExperiment.workspace_id.is_(None),
            ABExperiment.workspace_id == ws_id,
        )
    )
    exps2: list[ABExperiment] = list(
        db.session.scalars(
            select(ABExperiment).where(
                ABExperiment.status == ABExperimentStatus.completed.value,
                or_(
                    ABExperiment.variant_a_prompt_post_id.in_(all_post_ids),
                    ABExperiment.variant_b_prompt_post_id.in_(all_post_ids),
                ),
                exp_scope2,
            )
        ).all()
    )

    ab_win_rates: dict[int, float | None] = {}

    if exps2:
        exp_ids2 = [e.id for e in exps2]

        # ── Q5 ────────────────────────────────────────────────────────────
        exp_runs2: list[ABExperimentRun] = list(
            db.session.scalars(
                select(ABExperimentRun).where(
                    ABExperimentRun.experiment_id.in_(exp_ids2)
                )
            ).all()
        )

        all_run_ids2: set[int] = {er.run_a_id for er in exp_runs2} | {
            er.run_b_id for er in exp_runs2
        }

        # ── Q6 ────────────────────────────────────────────────────────────
        run_avg_map2: dict[int, float] = {}
        if all_run_ids2:
            run_avg_rows2 = db.session.execute(
                select(
                    BenchmarkRunResult.run_id,
                    func.avg(BenchmarkRunResult.score_numeric).label("avg"),
                )
                .where(
                    BenchmarkRunResult.run_id.in_(all_run_ids2),
                    BenchmarkRunResult.score_numeric.is_not(None),
                )
                .group_by(BenchmarkRunResult.run_id)
            ).all()
            run_avg_map2 = {
                r.run_id: float(r.avg) for r in run_avg_rows2 if r.avg is not None
            }

        exp_run_map2: dict[int, ABExperimentRun] = {
            er.experiment_id: er for er in exp_runs2
        }

        wins2: dict[int, int] = {}
        totals2: dict[int, int] = {}

        for exp in exps2:
            er = exp_run_map2.get(exp.id)
            if er is None:
                continue
            avg_a = run_avg_map2.get(er.run_a_id)
            avg_b = run_avg_map2.get(er.run_b_id)
            if avg_a is None or avg_b is None:
                continue

            pid_a = exp.variant_a_prompt_post_id
            pid_b = exp.variant_b_prompt_post_id

            for pid in (pid_a, pid_b):
                if pid in all_post_ids:
                    totals2[pid] = totals2.get(pid, 0) + 1

            if pid_a in all_post_ids and avg_a > avg_b:
                wins2[pid_a] = wins2.get(pid_a, 0) + 1
            if pid_b in all_post_ids and avg_b > avg_a:
                wins2[pid_b] = wins2.get(pid_b, 0) + 1

        for pid in all_post_ids:
            total = totals2.get(pid, 0)
            if total > 0:
                ab_win_rates[pid] = wins2.get(pid, 0) / total

    # ── Compute composite score denominators ──────────────────────────────
    max_bench = max(bench_avg_map.values(), default=0.0)
    max_votes = max((vote_map2.get(pid, 0) for pid in all_post_ids), default=0)
    max_ab = max((v for v in ab_win_rates.values() if v is not None), default=0.0)

    # ── Build entries ─────────────────────────────────────────────────────
    entries: list[ForkComparisonEntry] = []
    for is_origin, post in [(True, prompt), *((False, f) for f in fork_posts)]:
        bench_avg = bench_avg_map.get(post.id)
        votes = vote_map2.get(post.id, 0)
        ab_rate = ab_win_rates.get(post.id)

        norm_bench = (
            (bench_avg / max_bench)
            if (bench_avg is not None and max_bench > 0)
            else 0.0
        )
        norm_votes = (votes / max_votes) if max_votes > 0 else 0.0
        norm_ab = (ab_rate / max_ab) if (ab_rate is not None and max_ab > 0) else 0.0
        composite = round(0.60 * norm_bench + 0.30 * norm_votes + 0.10 * norm_ab, 6)

        entries.append(
            ForkComparisonEntry(
                post_id=post.id,
                title=post.title,
                slug=post.slug,
                kind=post.kind,
                scope="workspace" if post.workspace_id is not None else "public",
                is_origin=is_origin,
                benchmark_avg=round(bench_avg, 4) if bench_avg is not None else None,
                vote_count=votes,
                ab_win_rate=round(ab_rate, 4) if ab_rate is not None else None,
                composite_score=composite,
            )
        )

    # Deterministic descending sort: composite_score DESC, post_id DESC
    entries.sort(key=lambda e: (e.composite_score, e.post_id), reverse=True)

    origin_score = bench_avg_map.get(prompt.id)
    fork_bench_scores = [
        bench_avg_map[f.id] for f in fork_posts if f.id in bench_avg_map
    ]
    best_fork_score = (
        max(fork_bench_scores, default=None) if fork_bench_scores else None
    )

    return ForkComparison(
        origin_score=origin_score,
        best_fork_score=best_fork_score,
        fork_count=len(fork_posts),
        entries=entries,
    )
