"""Intelligence Dashboard Service — Cross-Family Benchmark Analytics.

Provides four aggregated views over benchmark data, each backed by a single SQL
query (4 queries total for a full page load — well within the ≤10 budget).

Functions
---------
``get_top_prompts(workspace, limit)``
    Top prompts by composite benchmark score over the last 30 days.

``get_most_improved(workspace, limit)``
    Prompts whose avg score improved most versus the prior 30-day window.
    Uses conditional aggregation (CASE WHEN) over a single 60-day span.

``get_ontology_performance(workspace)``
    Avg benchmark score + prompt count grouped by ontology category.

``get_fork_outperformance(workspace, limit)``
    Forks whose composite score exceeds their origin's composite score.

Scope Contract
--------------
Public  (workspace=None): only BenchmarkRun / Post rows with workspace_id IS NULL.
Workspace               : workspace_id IS NULL OR workspace_id == ws.id.
Enforcement is SQL-level only; no post-processing filter.

Determinism
-----------
Every ORDER BY includes a final ``post_id DESC`` (or ``node_id DESC``) tie-break
so results are stable across identical scores.

Window Boundaries (UTC)
-----------------------
current window : [now − 30 d, now)
previous window: [now − 60 d, now − 30 d)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import aliased

from backend.extensions import db
from backend.models.benchmark import (
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
)
from backend.models.content_link import ContentLink
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus

if TYPE_CHECKING:
    from backend.models.workspace import Workspace

_WINDOW_DAYS = 30


# ── Result dataclasses ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TopPromptRow:
    post_id: int
    title: str
    slug: str
    workspace_id: int | None
    composite_score: float
    ontology_node_name: str | None
    ontology_node_slug: str | None
    is_fork: bool


@dataclass(frozen=True)
class ImprovedPromptRow:
    post_id: int
    title: str
    slug: str
    workspace_id: int | None
    current_avg: float
    previous_avg: float
    delta: float


@dataclass(frozen=True)
class OntologyPerformanceRow:
    node_id: int
    node_name: str
    node_slug: str
    avg_score: float
    prompt_count: int


@dataclass(frozen=True)
class ForkOutperformanceRow:
    fork_post_id: int
    fork_title: str
    fork_slug: str
    fork_workspace_id: int | None
    origin_post_id: int
    origin_title: str
    origin_slug: str
    origin_workspace_id: int | None
    fork_score: float
    origin_score: float
    delta: float


# ── Scope expression helpers ──────────────────────────────────────────────────


def _post_scope(workspace: Workspace | None):
    if workspace is None:
        return Post.workspace_id.is_(None)
    return or_(Post.workspace_id.is_(None), Post.workspace_id == workspace.id)


def _run_scope(workspace: Workspace | None):
    if workspace is None:
        return BenchmarkRun.workspace_id.is_(None)
    return or_(
        BenchmarkRun.workspace_id.is_(None),
        BenchmarkRun.workspace_id == workspace.id,
    )


def _onto_scope(workspace: Workspace | None):
    if workspace is None:
        return ContentOntology.workspace_id.is_(None)
    return or_(
        ContentOntology.workspace_id.is_(None),
        ContentOntology.workspace_id == workspace.id,
    )


# ── Service functions ─────────────────────────────────────────────────────────


def get_top_prompts(
    workspace: Workspace | None = None,
    limit: int = 20,
) -> list[TopPromptRow]:
    """Return prompts ranked by avg benchmark score (last 30 days).

    One SQL query.  Tie-break: composite_score DESC, post.id DESC.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=_WINDOW_DAYS)

    # Deterministic first ontology node per post: MIN(node_id).
    onto_first = (
        select(
            ContentOntology.post_id.label("post_id"),
            func.min(ContentOntology.ontology_node_id).label("node_id"),
        )
        .where(_onto_scope(workspace))
        .group_by(ContentOntology.post_id)
        .subquery("onto_first")
    )

    avg_score_expr = func.avg(BenchmarkRunResult.score_numeric)

    is_fork_expr = exists(
        select(ContentLink.from_post_id).where(
            ContentLink.from_post_id == Post.id,
            ContentLink.link_type == "derived_from",
        )
    ).label("is_fork")

    stmt = (
        select(
            Post.id,
            Post.title,
            Post.slug,
            Post.workspace_id,
            avg_score_expr.label("composite_score"),
            OntologyNode.name.label("node_name"),
            OntologyNode.slug.label("node_slug"),
            is_fork_expr,
        )
        .join(BenchmarkRun, BenchmarkRun.prompt_post_id == Post.id)
        .join(BenchmarkRunResult, BenchmarkRunResult.run_id == BenchmarkRun.id)
        .outerjoin(onto_first, onto_first.c.post_id == Post.id)
        .outerjoin(OntologyNode, OntologyNode.id == onto_first.c.node_id)
        .where(
            BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            BenchmarkRun.created_at >= window_start,
            Post.kind == "prompt",
            Post.status == PostStatus.published.value,
            _post_scope(workspace),
            _run_scope(workspace),
            BenchmarkRunResult.score_numeric.is_not(None),
        )
        .group_by(
            Post.id,
            Post.title,
            Post.slug,
            Post.workspace_id,
            OntologyNode.name,
            OntologyNode.slug,
        )
        .order_by(avg_score_expr.desc(), Post.id.desc())
        .limit(limit)
    )

    rows = db.session.execute(stmt).all()
    return [
        TopPromptRow(
            post_id=r[0],
            title=r[1],
            slug=r[2],
            workspace_id=r[3],
            composite_score=float(r[4]),
            ontology_node_name=r[5],
            ontology_node_slug=r[6],
            is_fork=bool(r[7]),
        )
        for r in rows
    ]


def get_most_improved(
    workspace: Workspace | None = None,
    limit: int = 20,
) -> list[ImprovedPromptRow]:
    """Return prompts most improved vs the prior 30-day window.

    Uses conditional aggregation over 60 days — one SQL query.
    Only prompts with data in *both* windows whose delta > 0 are returned.
    Tie-break: delta DESC, post.id DESC.

    Note: SQL fetch is unbounded (no LIMIT); Python post-filters for delta > 0
    before slicing to ``limit``.  Acceptable for hundreds of prompts; add a
    HAVING clause if the dataset grows to thousands.
    """
    now = datetime.now(UTC)
    curr_start = now - timedelta(days=_WINDOW_DAYS)
    prev_start = now - timedelta(days=2 * _WINDOW_DAYS)

    current_avg = func.avg(
        case(
            (BenchmarkRun.created_at >= curr_start, BenchmarkRunResult.score_numeric),
            else_=None,
        )
    ).label("current_avg")

    previous_avg = func.avg(
        case(
            (
                (BenchmarkRun.created_at >= prev_start)
                & (BenchmarkRun.created_at < curr_start),
                BenchmarkRunResult.score_numeric,
            ),
            else_=None,
        )
    ).label("previous_avg")

    stmt = (
        select(
            Post.id,
            Post.title,
            Post.slug,
            Post.workspace_id,
            current_avg,
            previous_avg,
        )
        .join(BenchmarkRun, BenchmarkRun.prompt_post_id == Post.id)
        .join(BenchmarkRunResult, BenchmarkRunResult.run_id == BenchmarkRun.id)
        .where(
            BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            BenchmarkRun.created_at >= prev_start,
            Post.kind == "prompt",
            Post.status == PostStatus.published.value,
            _post_scope(workspace),
            _run_scope(workspace),
            BenchmarkRunResult.score_numeric.is_not(None),
        )
        .group_by(Post.id, Post.title, Post.slug, Post.workspace_id)
    )

    rows = db.session.execute(stmt).all()

    improved: list[ImprovedPromptRow] = []
    for r in rows:
        curr = r.current_avg
        prev = r.previous_avg
        if curr is None or prev is None:
            continue
        delta = float(curr) - float(prev)
        if delta <= 0:
            continue
        improved.append(
            ImprovedPromptRow(
                post_id=r.id,
                title=r.title,
                slug=r.slug,
                workspace_id=r.workspace_id,
                current_avg=float(curr),
                previous_avg=float(prev),
                delta=delta,
            )
        )

    improved.sort(key=lambda x: (-x.delta, -x.post_id))
    return improved[:limit]


def get_ontology_performance(
    workspace: Workspace | None = None,
) -> list[OntologyPerformanceRow]:
    """Return avg benchmark score + prompt count per ontology node (last 30 days).

    One SQL query.  Tie-break: avg_score DESC, node.id DESC.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=_WINDOW_DAYS)

    avg_expr = func.avg(BenchmarkRunResult.score_numeric)
    count_expr = func.count(func.distinct(Post.id))

    stmt = (
        select(
            OntologyNode.id,
            OntologyNode.name,
            OntologyNode.slug,
            avg_expr.label("avg_score"),
            count_expr.label("prompt_count"),
        )
        .select_from(OntologyNode)
        .join(ContentOntology, ContentOntology.ontology_node_id == OntologyNode.id)
        .join(Post, Post.id == ContentOntology.post_id)
        .join(BenchmarkRun, BenchmarkRun.prompt_post_id == Post.id)
        .join(BenchmarkRunResult, BenchmarkRunResult.run_id == BenchmarkRun.id)
        .where(
            BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            BenchmarkRun.created_at >= window_start,
            Post.kind == "prompt",
            Post.status == PostStatus.published.value,
            BenchmarkRunResult.score_numeric.is_not(None),
            _post_scope(workspace),
            _run_scope(workspace),
            _onto_scope(workspace),
        )
        .group_by(OntologyNode.id, OntologyNode.name, OntologyNode.slug)
        .order_by(avg_expr.desc(), OntologyNode.id.desc())
    )

    rows = db.session.execute(stmt).all()
    return [
        OntologyPerformanceRow(
            node_id=r[0],
            node_name=r[1],
            node_slug=r[2],
            avg_score=float(r[3]),
            prompt_count=r[4],
        )
        for r in rows
    ]


def get_fork_outperformance(
    workspace: Workspace | None = None,
    limit: int = 20,
) -> list[ForkOutperformanceRow]:
    """Return forks whose composite benchmark score exceeds their origin's score.

    One SQL query using two aliases of the same scores subquery.
    Tie-break: delta DESC, fork_post.id DESC.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=_WINDOW_DAYS)

    # Base scores subquery — reused for fork and origin via aliasing.
    _scores_base = (
        select(
            BenchmarkRun.prompt_post_id.label("post_id"),
            func.avg(BenchmarkRunResult.score_numeric).label("avg_score"),
        )
        .join(BenchmarkRunResult, BenchmarkRunResult.run_id == BenchmarkRun.id)
        .where(
            BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            BenchmarkRun.created_at >= window_start,
            _run_scope(workspace),
            BenchmarkRunResult.score_numeric.is_not(None),
        )
        .group_by(BenchmarkRun.prompt_post_id)
    )
    fork_scores = _scores_base.subquery("fork_scores")
    origin_scores = _scores_base.subquery("origin_scores")

    fork_post = aliased(Post, name="fork_p")
    origin_post = aliased(Post, name="origin_p")

    if workspace is None:
        fork_scope = fork_post.workspace_id.is_(None)
        origin_scope = origin_post.workspace_id.is_(None)
    else:
        fork_scope = or_(
            fork_post.workspace_id.is_(None),
            fork_post.workspace_id == workspace.id,
        )
        origin_scope = or_(
            origin_post.workspace_id.is_(None),
            origin_post.workspace_id == workspace.id,
        )

    delta_expr = (fork_scores.c.avg_score - origin_scores.c.avg_score).label("delta")

    stmt = (
        select(
            fork_post.id.label("fork_post_id"),
            fork_post.title.label("fork_title"),
            fork_post.slug.label("fork_slug"),
            fork_post.workspace_id.label("fork_workspace_id"),
            origin_post.id.label("origin_post_id"),
            origin_post.title.label("origin_title"),
            origin_post.slug.label("origin_slug"),
            origin_post.workspace_id.label("origin_workspace_id"),
            fork_scores.c.avg_score.label("fork_score"),
            origin_scores.c.avg_score.label("origin_score"),
            delta_expr,
        )
        .select_from(ContentLink)
        .join(fork_post, fork_post.id == ContentLink.from_post_id)
        .join(origin_post, origin_post.id == ContentLink.to_post_id)
        .join(fork_scores, fork_scores.c.post_id == fork_post.id)
        .join(origin_scores, origin_scores.c.post_id == origin_post.id)
        .where(
            ContentLink.link_type == "derived_from",
            fork_scores.c.avg_score > origin_scores.c.avg_score,
            fork_post.kind == "prompt",
            fork_post.status == PostStatus.published.value,
            origin_post.kind == "prompt",
            origin_post.status == PostStatus.published.value,
            fork_scope,
            origin_scope,
        )
        .order_by(delta_expr.desc(), fork_post.id.desc())
        .limit(limit)
    )

    rows = db.session.execute(stmt).all()
    return [
        ForkOutperformanceRow(
            fork_post_id=r.fork_post_id,
            fork_title=r.fork_title,
            fork_slug=r.fork_slug,
            fork_workspace_id=r.fork_workspace_id,
            origin_post_id=r.origin_post_id,
            origin_title=r.origin_title,
            origin_slug=r.origin_slug,
            origin_workspace_id=r.origin_workspace_id,
            fork_score=float(r.fork_score),
            origin_score=float(r.origin_score),
            delta=float(r.delta),
        )
        for r in rows
    ]
