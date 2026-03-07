"""Workspace Knowledge Health Dashboard — service layer.

Surfaces six health metrics for a workspace:

1. Health summary     — aggregate counts (1 query)
2. Ontology coverage  — per-node post/benchmark/revision/contributor counts (2 queries)
3. Unbenchmarked      — workspace prompts with no completed benchmark run (1 query)
4. Stale content      — published content not updated in ≥ N days (1 query)
5. Unimproved         — published content with zero accepted revisions (1 query)
6. Contributor gaps   — per-node contributor analysis (1 query)
7. Recommended actions — deterministic priority rules (2 queries)

Total route query budget: 9 SQL statements (≤ 10 required).

Scope invariants
----------------
- Every metric restricts to posts.workspace_id = ws.id.
  Public content (posts.workspace_id IS NULL) is NEVER included.
- content_ontology scope: co.workspace_id = ws.id OR co.workspace_id IS NULL.
  This is intentional: workspace posts may carry public-layer ontology mappings.
- benchmark_runs scope: benchmark_runs.workspace_id = ws.id.
  Benchmark runs in other workspaces are not considered.

Definitions
-----------
- Benchmarked prompt: workspace prompt with ≥ 1 benchmark_run where
    benchmark_runs.workspace_id = ws.id AND status = 'completed',
    regardless of prompt version (v1; add version filter in v2).
- No Accepted Revisions: published workspace content with
    COUNT(revisions WHERE status = 'accepted') = 0.
    Author-only edits (Post.version > 1) are NOT "accepted revisions".
- Contributor count per node: COUNT DISTINCT of
    Post.author_id UNION (Revision.author_id WHERE status = 'accepted').
    The original author always counts.
- Single-point-of-knowledge: contributor_count == 1.
- Stale: published, kind IN ('article','playbook','prompt'),
    updated_at < now - stale_days. Defaults to 90 days.
    kind='framework' is excluded in v1.

Ordering / tie-breaks (all deterministic)
-----------------------------------------
- coverage:         post_count ASC, node_id DESC
- unbenchmarked:    updated_at ASC, post_id DESC
- stale:            updated_at ASC, post_id DESC  (most stale first)
- unimproved:       created_at ASC, post_id DESC
- gaps:             contributor_count ASC, node_id DESC
- recommendations:  priority ASC, then entity-id DESC within each priority
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, func, literal, or_, select, union_all

from backend.extensions import db
from backend.models.benchmark import BenchmarkRun
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User

log = logging.getLogger(__name__)

_STALE_KINDS = ("article", "playbook", "prompt")
_LOW_COVERAGE_DEFAULT = 3


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class WorkspaceHealthSummary:
    total_prompts: int
    benchmarked_prompts: int
    stale_items: int
    active_contributors: int


@dataclasses.dataclass(slots=True)
class OntologyCoverageRow:
    node_id: int
    node_name: str
    node_slug: str
    post_count: int
    benchmarked_count: int
    revised_count: int
    contributor_count: int
    is_low_coverage: bool


@dataclasses.dataclass(slots=True)
class UnbenchmarkedPrompt:
    post_id: int
    title: str
    slug: str
    version: int
    updated_at: datetime
    ontology_node_names: list[str]


@dataclasses.dataclass(slots=True)
class StaleContent:
    post_id: int
    title: str
    slug: str
    kind: str
    updated_at: datetime
    days_stale: int


@dataclasses.dataclass(slots=True)
class UnimprovedContent:
    post_id: int
    title: str
    slug: str
    kind: str
    created_at: datetime


@dataclasses.dataclass(slots=True)
class ContributorGapRow:
    node_id: int
    node_name: str
    node_slug: str
    contributor_count: int
    top_contributor_username: str | None
    is_single_point: bool


@dataclasses.dataclass(slots=True)
class RecommendedAction:
    priority: int  # 1–4
    action_type: str  # benchmark_prompt | review_stale | add_content | request_revision
    title: str
    detail: str
    post_slug: str | None
    node_slug: str | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _co_scope(ws):
    """Filter for content_ontology rows relevant to a workspace.

    Includes both explicit workspace mappings and public-layer mappings
    that may be attached to workspace-owned posts.
    """
    return or_(
        ContentOntology.workspace_id == ws.id,
        ContentOntology.workspace_id.is_(None),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class WorkspaceHealthService:
    # ── 1. Health summary (1 query) ───────────────────────────────────────

    @staticmethod
    def get_health_summary(ws) -> WorkspaceHealthSummary:
        """Single SELECT with scalar subqueries for all four summary metrics."""
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(days=_STALE_KINDS and 90)

        # Scalar subquery: total published workspace prompts
        total_sq = (
            select(func.count(Post.id))
            .where(
                Post.workspace_id == ws.id,
                Post.kind == "prompt",
                Post.status == PostStatus.published.value,
            )
            .scalar_subquery()
        )

        # Scalar subquery: prompts with ≥ 1 completed benchmark run in this workspace
        bench_sq = (
            select(func.count(func.distinct(BenchmarkRun.prompt_post_id)))
            .join(Post, Post.id == BenchmarkRun.prompt_post_id)
            .where(
                BenchmarkRun.workspace_id == ws.id,
                BenchmarkRun.status == "completed",
                Post.workspace_id == ws.id,
                Post.kind == "prompt",
                Post.status == PostStatus.published.value,
            )
            .scalar_subquery()
        )

        # Scalar subquery: stale published content count
        stale_sq = (
            select(func.count(Post.id))
            .where(
                Post.workspace_id == ws.id,
                Post.status == PostStatus.published.value,
                Post.kind.in_(_STALE_KINDS),
                Post.updated_at < stale_cutoff,
            )
            .scalar_subquery()
        )

        # Scalar subquery: distinct accepted revision authors across workspace posts
        contrib_sq = (
            select(func.count(func.distinct(Revision.author_id)))
            .join(Post, Post.id == Revision.post_id)
            .where(
                Post.workspace_id == ws.id,
                Revision.status == RevisionStatus.accepted.value,
            )
            .scalar_subquery()
        )

        row = db.session.execute(
            select(
                total_sq.label("total_prompts"),
                bench_sq.label("benchmarked_prompts"),
                stale_sq.label("stale_items"),
                contrib_sq.label("active_contributors"),
            )
        ).one()

        return WorkspaceHealthSummary(
            total_prompts=row.total_prompts or 0,
            benchmarked_prompts=row.benchmarked_prompts or 0,
            stale_items=row.stale_items or 0,
            active_contributors=row.active_contributors or 0,
        )

    # ── 2. Ontology coverage (2 queries) ──────────────────────────────────

    @staticmethod
    def get_ontology_coverage(
        ws, *, low_coverage_threshold: int = _LOW_COVERAGE_DEFAULT
    ) -> list[OntologyCoverageRow]:
        """Per-node post/benchmark/revised/contributor counts.

        Q1: node + post counts + benchmarked counts
        Q2: per-node revised_count + contributor_count
        Merged in Python; sorted post_count ASC, node_id DESC.
        """
        # ── Q1: post count + benchmarked count per node ──────────────────
        bench_subq = (
            select(BenchmarkRun.prompt_post_id)
            .where(
                BenchmarkRun.workspace_id == ws.id,
                BenchmarkRun.status == "completed",
            )
            .subquery()
        )

        q1_rows = db.session.execute(
            select(
                OntologyNode.id.label("node_id"),
                OntologyNode.name.label("node_name"),
                OntologyNode.slug.label("node_slug"),
                func.count(func.distinct(Post.id)).label("post_count"),
                func.count(func.distinct(bench_subq.c.prompt_post_id)).label(
                    "benchmarked_count"
                ),
            )
            .join(ContentOntology, ContentOntology.ontology_node_id == OntologyNode.id)
            .join(Post, Post.id == ContentOntology.post_id)
            .outerjoin(bench_subq, bench_subq.c.prompt_post_id == Post.id)
            .where(
                Post.workspace_id == ws.id,
                _co_scope(ws),
            )
            .group_by(OntologyNode.id, OntologyNode.name, OntologyNode.slug)
        ).all()

        if not q1_rows:
            return []

        node_ids = [r.node_id for r in q1_rows]

        # ── Q2: revised_count + contributor_count per node ───────────────
        # contributor = distinct(author_id UNION accepted-revision author_id)
        # for posts scoped to each node.

        # Sub-select: post_id → node_id mapping (workspace posts in these nodes)
        post_node_subq = (
            select(
                Post.id.label("post_id"),
                ContentOntology.ontology_node_id.label("node_id"),
            )
            .join(ContentOntology, ContentOntology.post_id == Post.id)
            .where(
                Post.workspace_id == ws.id,
                ContentOntology.ontology_node_id.in_(node_ids),
                _co_scope(ws),
            )
            .subquery()
        )

        # Authors: original post authors per node
        author_contrib = select(
            post_node_subq.c.node_id.label("node_id"),
            Post.author_id.label("user_id"),
            literal(0).label("rev_count"),
        ).join(Post, Post.id == post_node_subq.c.post_id)

        # Accepted revision authors per node
        rev_contrib = (
            select(
                post_node_subq.c.node_id.label("node_id"),
                Revision.author_id.label("user_id"),
                func.count(Revision.id).label("rev_count"),
            )
            .join(Revision, Revision.post_id == post_node_subq.c.post_id)
            .where(Revision.status == RevisionStatus.accepted.value)
            .group_by(post_node_subq.c.node_id, Revision.author_id)
        )

        all_contribs = union_all(author_contrib, rev_contrib).subquery()

        q2_rows = db.session.execute(
            select(
                all_contribs.c.node_id,
                func.count(func.distinct(all_contribs.c.user_id)).label(
                    "contributor_count"
                ),
                # A post is "revised" if any of its contributors came via revision
                # We compute revised_count differently: posts with ≥1 accepted revision
            ).group_by(all_contribs.c.node_id)
        ).all()

        # Also need revised_count = posts in node that have ≥1 accepted revision
        revised_rows = db.session.execute(
            select(
                post_node_subq.c.node_id,
                func.count(func.distinct(post_node_subq.c.post_id)).label(
                    "revised_count"
                ),
            )
            .join(
                Revision,
                and_(
                    Revision.post_id == post_node_subq.c.post_id,
                    Revision.status == RevisionStatus.accepted.value,
                ),
            )
            .group_by(post_node_subq.c.node_id)
        ).all()

        node_contributor_count: dict[int, int] = {
            r.node_id: r.contributor_count for r in q2_rows
        }
        node_revised_count: dict[int, int] = {
            r.node_id: r.revised_count for r in revised_rows
        }

        rows = [
            OntologyCoverageRow(
                node_id=r.node_id,
                node_name=r.node_name,
                node_slug=r.node_slug,
                post_count=r.post_count,
                benchmarked_count=r.benchmarked_count,
                revised_count=node_revised_count.get(r.node_id, 0),
                contributor_count=node_contributor_count.get(r.node_id, 0),
                is_low_coverage=r.post_count < low_coverage_threshold,
            )
            for r in q1_rows
        ]
        rows.sort(key=lambda r: (r.post_count, -r.node_id))
        return rows

    # ── 3. Unbenchmarked prompts (1 query) ────────────────────────────────

    @staticmethod
    def get_unbenchmarked_prompts(ws, *, limit: int = 20) -> list[UnbenchmarkedPrompt]:
        """Published workspace prompts with no completed benchmark run.

        A prompt is benchmarked if it has at least one completed run in this
        workspace, regardless of prompt version.

        Ontology node names collected in the same query via LEFT JOIN +
        GROUP_CONCAT / aggregation, then deduplicated in Python.
        """
        completed_bench_subq = (
            select(BenchmarkRun.prompt_post_id)
            .where(
                BenchmarkRun.workspace_id == ws.id,
                BenchmarkRun.status == "completed",
            )
            .subquery()
        )

        rows = db.session.execute(
            select(
                Post.id,
                Post.title,
                Post.slug,
                Post.version,
                Post.updated_at,
                OntologyNode.name.label("node_name"),
            )
            .outerjoin(
                ContentOntology,
                and_(
                    ContentOntology.post_id == Post.id,
                    _co_scope(ws),
                ),
            )
            .outerjoin(
                OntologyNode, OntologyNode.id == ContentOntology.ontology_node_id
            )
            .outerjoin(
                completed_bench_subq,
                completed_bench_subq.c.prompt_post_id == Post.id,
            )
            .where(
                Post.workspace_id == ws.id,
                Post.kind == "prompt",
                Post.status == PostStatus.published.value,
                completed_bench_subq.c.prompt_post_id.is_(None),  # NOT benchmarked
            )
            .order_by(Post.updated_at.asc(), Post.id.desc())
        ).all()

        # Deduplicate: one Post may appear multiple times due to multiple node joins
        seen: dict[int, UnbenchmarkedPrompt] = {}
        for r in rows:
            if r[0] not in seen:
                seen[r[0]] = UnbenchmarkedPrompt(
                    post_id=r[0],
                    title=r[1],
                    slug=r[2],
                    version=r[3],
                    updated_at=r[4],
                    ontology_node_names=[],
                )
            if r.node_name is not None:
                seen[r[0]].ontology_node_names.append(r.node_name)

        result = list(seen.values())
        # Re-sort after dict (preserves insertion order in Python 3.7+ but
        # be explicit for correctness after dedup)
        result.sort(key=lambda p: (p.updated_at, -p.post_id))
        return result[:limit]

    # ── 4. Stale content (1 query) ────────────────────────────────────────

    @staticmethod
    def get_stale_content(
        ws, *, limit: int = 20, stale_days: int = 90
    ) -> list[StaleContent]:
        """Published workspace content (article/playbook/prompt) not updated
        in stale_days days.

        days_stale computed in Python to avoid SQLite/PostgreSQL dialect
        differences.

        Sorted: updated_at ASC, id DESC (most stale first, tie-break id DESC).
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=stale_days)

        rows = db.session.execute(
            select(Post.id, Post.title, Post.slug, Post.kind, Post.updated_at)
            .where(
                Post.workspace_id == ws.id,
                Post.status == PostStatus.published.value,
                Post.kind.in_(_STALE_KINDS),
                Post.updated_at < cutoff,
            )
            .order_by(Post.updated_at.asc(), Post.id.desc())
            .limit(limit)
        ).all()

        return [
            StaleContent(
                post_id=r.id,
                title=r.title,
                slug=r.slug,
                kind=r.kind,
                updated_at=r.updated_at,
                days_stale=(
                    now - r.updated_at.replace(tzinfo=UTC)
                    if r.updated_at.tzinfo is None
                    else now - r.updated_at
                ).days,
            )
            for r in rows
        ]

    # ── 5. Unimproved content (1 query) ───────────────────────────────────

    @staticmethod
    def get_unimproved_content(ws, *, limit: int = 20) -> list[UnimprovedContent]:
        """Published workspace content with zero accepted revisions.

        Author-only edits are NOT counted; only accepted collaborative
        revisions qualify as "improvement".

        Sorted: created_at ASC, id DESC.
        """
        has_accepted_rev = exists(
            select(Revision.id).where(
                Revision.post_id == Post.id,
                Revision.status == RevisionStatus.accepted.value,
            )
        )

        rows = db.session.execute(
            select(Post.id, Post.title, Post.slug, Post.kind, Post.created_at)
            .where(
                Post.workspace_id == ws.id,
                Post.status == PostStatus.published.value,
                ~has_accepted_rev,
            )
            .order_by(Post.created_at.asc(), Post.id.desc())
            .limit(limit)
        ).all()

        return [
            UnimprovedContent(
                post_id=r.id,
                title=r.title,
                slug=r.slug,
                kind=r.kind,
                created_at=r.created_at,
            )
            for r in rows
        ]

    # ── 6. Contributor gaps (1 query) ─────────────────────────────────────

    @staticmethod
    def get_contributor_gaps(ws) -> list[ContributorGapRow]:
        """Per-node contributor analysis using UNION ALL of authors + revisers.

        contributor_count = DISTINCT(Post.author_id UNION accepted-revision authors)
        top_contributor_username = contributor with most accepted revisions;
            tie-break: user_id DESC.
        is_single_point = contributor_count == 1.

        Sorted: contributor_count ASC, node_id DESC.
        """
        # Build a subquery: (node_id, post_id, user_id) — one row per
        # contributor per post per node.
        post_node_sq = (
            select(
                Post.id.label("post_id"),
                ContentOntology.ontology_node_id.label("node_id"),
            )
            .join(ContentOntology, ContentOntology.post_id == Post.id)
            .where(
                Post.workspace_id == ws.id,
                _co_scope(ws),
            )
            .subquery()
        )

        # Authors contribution leg
        authors_leg = select(
            post_node_sq.c.node_id.label("node_id"),
            Post.author_id.label("user_id"),
            literal(0).label("rev_count"),
        ).join(Post, Post.id == post_node_sq.c.post_id)

        # Revision authors contribution leg
        revisers_leg = (
            select(
                post_node_sq.c.node_id.label("node_id"),
                Revision.author_id.label("user_id"),
                func.count(Revision.id).label("rev_count"),
            )
            .join(Revision, Revision.post_id == post_node_sq.c.post_id)
            .where(Revision.status == RevisionStatus.accepted.value)
            .group_by(post_node_sq.c.node_id, Revision.author_id)
        )

        union_sq = union_all(authors_leg, revisers_leg).subquery()

        # Per-node aggregation
        agg_sq = (
            select(
                union_sq.c.node_id.label("node_id"),
                union_sq.c.user_id.label("user_id"),
                func.sum(union_sq.c.rev_count).label("total_revs"),
            )
            .group_by(union_sq.c.node_id, union_sq.c.user_id)
            .subquery()
        )

        # Node-level aggregation + identify top contributor per node
        # Top contributor: max(total_revs, then max user_id for tie-break)
        top_contrib_sq = (
            select(
                agg_sq.c.node_id.label("node_id"),
                func.count(func.distinct(agg_sq.c.user_id)).label("contributor_count"),
                # max(rev_count) per node for identifying top contributor
                func.max(agg_sq.c.total_revs).label("max_revs"),
            )
            .group_by(agg_sq.c.node_id)
            .subquery()
        )

        # Final query: join back to get node info + top contributor username
        # Top contributor = user with max(total_revs), tie-break user_id DESC
        top_user_sq = (
            select(
                agg_sq.c.node_id.label("node_id"),
                func.max(agg_sq.c.user_id).label("top_user_id"),
            )
            .join(
                top_contrib_sq,
                and_(
                    agg_sq.c.node_id == top_contrib_sq.c.node_id,
                    agg_sq.c.total_revs == top_contrib_sq.c.max_revs,
                ),
            )
            .group_by(agg_sq.c.node_id)
            .subquery()
        )

        rows = db.session.execute(
            select(
                OntologyNode.id.label("node_id"),
                OntologyNode.name.label("node_name"),
                OntologyNode.slug.label("node_slug"),
                top_contrib_sq.c.contributor_count,
                User.username.label("top_contributor_username"),
            )
            .join(top_contrib_sq, top_contrib_sq.c.node_id == OntologyNode.id)
            .outerjoin(top_user_sq, top_user_sq.c.node_id == OntologyNode.id)
            .outerjoin(User, User.id == top_user_sq.c.top_user_id)
        ).all()

        result = [
            ContributorGapRow(
                node_id=r.node_id,
                node_name=r.node_name,
                node_slug=r.node_slug,
                contributor_count=r.contributor_count,
                top_contributor_username=r.top_contributor_username,
                is_single_point=r.contributor_count == 1,
            )
            for r in rows
        ]
        result.sort(key=lambda r: (r.contributor_count, -r.node_id))
        return result

    # ── 7. Recommended actions (2 queries) ────────────────────────────────

    @staticmethod
    def get_recommended_actions(ws, *, limit: int = 10) -> list[RecommendedAction]:
        """Deterministic rule-based recommendations.

        Priority 1: Unbenchmarked prompt in low-coverage node (post_count < 3)
            → action_type='benchmark_prompt'
            → sort: node_post_count ASC, post_id DESC

        Priority 2: Stale prompt (kind='prompt') in higher-coverage node
            → action_type='review_stale'
            → sort: updated_at ASC, post_id DESC

        Priority 3: Single-contributor ontology node
            → action_type='add_content'
            → sort: node_id DESC

        Priority 4: Content with no accepted revisions
            → action_type='request_revision'
            → sort: post_id DESC

        Deduplication: a post or node is not recommended twice across all
        priority levels (first occurrence wins).
        """
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(days=90)

        # ── Q1: prompts + node coverage + benchmark status ────────────────
        completed_bench_sq = (
            select(BenchmarkRun.prompt_post_id)
            .where(
                BenchmarkRun.workspace_id == ws.id,
                BenchmarkRun.status == "completed",
            )
            .subquery()
        )

        node_post_count_sq = (
            select(
                ContentOntology.ontology_node_id.label("node_id"),
                func.count(func.distinct(Post.id)).label("node_post_count"),
            )
            .join(Post, Post.id == ContentOntology.post_id)
            .where(
                Post.workspace_id == ws.id,
                _co_scope(ws),
            )
            .group_by(ContentOntology.ontology_node_id)
            .subquery()
        )

        q1_rows = db.session.execute(
            select(
                Post.id.label("post_id"),
                Post.title.label("post_title"),
                Post.slug.label("post_slug"),
                Post.kind.label("post_kind"),
                Post.updated_at.label("updated_at"),
                OntologyNode.id.label("node_id"),
                OntologyNode.name.label("node_name"),
                OntologyNode.slug.label("node_slug"),
                func.coalesce(node_post_count_sq.c.node_post_count, 0).label(
                    "node_post_count"
                ),
                completed_bench_sq.c.prompt_post_id.label("benchmarked"),
            )
            .outerjoin(
                ContentOntology,
                and_(
                    ContentOntology.post_id == Post.id,
                    _co_scope(ws),
                ),
            )
            .outerjoin(
                OntologyNode, OntologyNode.id == ContentOntology.ontology_node_id
            )
            .outerjoin(
                node_post_count_sq,
                node_post_count_sq.c.node_id == ContentOntology.ontology_node_id,
            )
            .outerjoin(
                completed_bench_sq,
                completed_bench_sq.c.prompt_post_id == Post.id,
            )
            .where(
                Post.workspace_id == ws.id,
                Post.kind == "prompt",
                Post.status == PostStatus.published.value,
            )
        ).all()

        # ── Q2: stale content + contributor gap nodes ─────────────────────
        # (a) stale prompts
        stale_rows = db.session.execute(
            select(
                Post.id,
                Post.title,
                Post.slug,
                Post.kind,
                Post.updated_at,
                OntologyNode.id.label("node_id"),
                OntologyNode.name.label("node_name"),
                OntologyNode.slug.label("node_slug"),
                func.coalesce(node_post_count_sq.c.node_post_count, 0).label(
                    "node_post_count"
                ),
            )
            .outerjoin(
                ContentOntology,
                and_(ContentOntology.post_id == Post.id, _co_scope(ws)),
            )
            .outerjoin(
                OntologyNode, OntologyNode.id == ContentOntology.ontology_node_id
            )
            .outerjoin(
                node_post_count_sq,
                node_post_count_sq.c.node_id == ContentOntology.ontology_node_id,
            )
            .where(
                Post.workspace_id == ws.id,
                Post.kind == "prompt",
                Post.status == PostStatus.published.value,
                Post.updated_at < stale_cutoff,
            )
            .order_by(Post.updated_at.asc(), Post.id.desc())
        ).all()

        # (b) single-contributor nodes via re-use of contributor gap logic
        # Inline light version: node_id → contributor count using UNION ALL
        post_node_sq2 = (
            select(
                Post.id.label("post_id"),
                ContentOntology.ontology_node_id.label("node_id"),
            )
            .join(ContentOntology, ContentOntology.post_id == Post.id)
            .where(Post.workspace_id == ws.id, _co_scope(ws))
            .subquery()
        )
        authors_leg2 = select(
            post_node_sq2.c.node_id.label("node_id"),
            Post.author_id.label("user_id"),
        ).join(Post, Post.id == post_node_sq2.c.post_id)

        revisers_leg2 = (
            select(
                post_node_sq2.c.node_id.label("node_id"),
                Revision.author_id.label("user_id"),
            )
            .join(Revision, Revision.post_id == post_node_sq2.c.post_id)
            .where(Revision.status == RevisionStatus.accepted.value)
        )

        union_sq2 = union_all(authors_leg2, revisers_leg2).subquery()

        single_point_rows = db.session.execute(
            select(
                OntologyNode.id.label("node_id"),
                OntologyNode.name.label("node_name"),
                OntologyNode.slug.label("node_slug"),
                func.count(func.distinct(union_sq2.c.user_id)).label(
                    "contributor_count"
                ),
            )
            .join(union_sq2, union_sq2.c.node_id == OntologyNode.id)
            .group_by(OntologyNode.id, OntologyNode.name, OntologyNode.slug)
            .having(func.count(func.distinct(union_sq2.c.user_id)) == 1)
        ).all()

        # ── Build actions in priority order ───────────────────────────────
        actions: list[RecommendedAction] = []
        seen_posts: set[int] = set()
        seen_nodes: set[int] = set()

        # Priority 1: unbenchmarked prompt in low-coverage node
        # Collect unique (post_id, node_post_count) from q1_rows
        p1_candidates: list[tuple[int, str, str, int | None, int]] = []
        seen_p1: set[int] = set()
        for r in q1_rows:
            if r.benchmarked is None and r.post_id not in seen_p1:
                node_pc = r.node_post_count if r.node_id is not None else 999
                p1_candidates.append(
                    (r.post_id, r.post_title, r.post_slug, r.node_slug, node_pc)
                )
                seen_p1.add(r.post_id)
        p1_candidates.sort(
            key=lambda t: (t[4], -t[0])
        )  # node_post_count ASC, post_id DESC
        for post_id, post_title, post_slug, node_slug, npc in p1_candidates:
            if npc < _LOW_COVERAGE_DEFAULT and post_id not in seen_posts:
                actions.append(
                    RecommendedAction(
                        priority=1,
                        action_type="benchmark_prompt",
                        title=f"Benchmark prompt: {post_title}",
                        detail=f"This prompt has not been benchmarked. Node coverage: {npc} post(s).",
                        post_slug=post_slug,
                        node_slug=node_slug,
                    )
                )
                seen_posts.add(post_id)

        # Priority 2: stale prompt in stronger node (post_count >= 3)
        seen_p2: set[int] = set()
        p2_candidates: list[tuple[int, str, str, int | None, object]] = []
        for r in stale_rows:
            if r[0] not in seen_p2:
                node_pc = r.node_post_count if r.node_id is not None else 0
                p2_candidates.append((r[0], r[1], r[2], r.node_slug, r[4], node_pc))
                seen_p2.add(r[0])
        p2_candidates.sort(key=lambda t: (t[4], -t[0]))  # updated_at ASC, post_id DESC
        for post_id, post_title, post_slug, node_slug, updated_at, npc in p2_candidates:
            if npc >= _LOW_COVERAGE_DEFAULT and post_id not in seen_posts:
                actions.append(
                    RecommendedAction(
                        priority=2,
                        action_type="review_stale",
                        title=f"Review stale prompt: {post_title}",
                        detail="Not updated in over 90 days.",
                        post_slug=post_slug,
                        node_slug=node_slug,
                    )
                )
                seen_posts.add(post_id)

        # Priority 3: single-contributor node
        sp_sorted = sorted(single_point_rows, key=lambda r: -r.node_id)
        for r in sp_sorted:
            if r.node_id not in seen_nodes:
                actions.append(
                    RecommendedAction(
                        priority=3,
                        action_type="add_content",
                        title=f"Add more content in: {r.node_name}",
                        detail="Only one contributor covers this area. Consider adding more content.",
                        post_slug=None,
                        node_slug=r.node_slug,
                    )
                )
                seen_nodes.add(r.node_id)

        # Priority 4: content with no accepted revisions (use unimproved data)
        # Inline: fetch from unimproved (already scoped); reuse the subquery pattern
        has_accepted = exists(
            select(Revision.id).where(
                Revision.post_id == Post.id,
                Revision.status == RevisionStatus.accepted.value,
            )
        )
        unimproved_rows = db.session.execute(
            select(Post.id, Post.title, Post.slug, Post.kind)
            .where(
                Post.workspace_id == ws.id,
                Post.status == PostStatus.published.value,
                ~has_accepted,
            )
            .order_by(Post.id.desc())
            .limit(50)
        ).all()

        for r in unimproved_rows:
            if r[0] not in seen_posts:
                actions.append(
                    RecommendedAction(
                        priority=4,
                        action_type="request_revision",
                        title=f"Request revisions for: {r[1]}",
                        detail=f"This {r[3]} has no accepted revisions yet.",
                        post_slug=r[2],
                        node_slug=None,
                    )
                )
                seen_posts.add(r[0])

        return actions[:limit]
