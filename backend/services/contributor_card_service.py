"""Contributor Card service — Top Improver ranking cards.

Surfaces the strongest contributors for a given context:
  * Global public ranking
  * Per-ontology-node ranking (public or workspace)
  * Per-prompt-family ranking (public or workspace)

Improver Score Formula
----------------------
improver_score =
    0.60 * norm_accepted_revisions
  + 0.20 * norm_benchmark_improvements
  + 0.10 * norm_ab_wins
  + 0.10 * norm_ontology_breadth

All normalisation is min-max within the candidate set
(max-anchored: max→1.0, 0→0.0).

Tie-break: improver_score DESC, user_id DESC (deterministic).

Scope rules
-----------
Public:
  Only workspace_id IS NULL contributions; only public badges.

Workspace:
  Contributions where workspace_id IS NULL OR workspace_id = ws.id.
  Candidates are already filtered to users who contributed in scope;
  this service never calls get_workspace_for_user — the route is
  responsible for member gating.

Query budget
------------
All three public functions execute at most 7 SQL statements:
  1. Descendant IDs / family posts discovery (ontology / prompt only)
  2. Candidate user_id discovery (scoped)
  3. Accepted revisions per user        ┐
  4. Reputation events per user         │  _compute_candidate_metrics
  5. Ontology breadth per user          │  (4 queries)
  6. User details (username, avatars)   ┘
  7. Top-3 public badge keys per user
"""

from __future__ import annotations

import dataclasses
import logging

from sqlalchemy import func, or_, select

from backend.extensions import db
from backend.models.badge import Badge, UserBadge
from backend.models.content_link import ContentLink
from backend.models.ontology import ContentOntology
from backend.models.post import Post, PostStatus
from backend.models.reputation_event import ReputationEvent
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User

log = logging.getLogger(__name__)

# Weights must sum to 1.0.
_W_REVISIONS = 0.60
_W_BENCHMARKS = 0.20
_W_AB_WINS = 0.10
_W_ONTOLOGY = 0.10

# Max candidates evaluated per call (prevents full-table scans on large DBs).
_MAX_CANDIDATES = 200


# ---------------------------------------------------------------------------
# Public DTO
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class ContributorCard:
    """A ranked contributor entry."""

    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    improver_score: float
    accepted_revisions: int
    benchmark_improvements: int
    ab_wins: int
    ontology_breadth: int
    badge_keys: list[str]
    rank: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize(values: dict[int, float]) -> dict[int, float]:
    """Min-max normalise *values* within the set; max→1.0, min/0→0.0."""
    if not values:
        return {}
    max_val = max(values.values())
    if max_val == 0:
        return {uid: 0.0 for uid in values}
    return {uid: v / max_val for uid, v in values.items()}


def _scope_filter_post(ws):
    """SQLAlchemy filter expression for Post.workspace_id scope."""
    if ws is None:
        return Post.workspace_id.is_(None)
    return or_(Post.workspace_id.is_(None), Post.workspace_id == ws.id)


def _scope_filter_co(ws):
    """SQLAlchemy filter expression for ContentOntology.workspace_id scope."""
    if ws is None:
        return ContentOntology.workspace_id.is_(None)
    return or_(
        ContentOntology.workspace_id.is_(None),
        ContentOntology.workspace_id == ws.id,
    )


def _scope_filter_re(ws):
    """SQLAlchemy filter expression for ReputationEvent.workspace_id scope."""
    if ws is None:
        return ReputationEvent.workspace_id.is_(None)
    return or_(
        ReputationEvent.workspace_id.is_(None),
        ReputationEvent.workspace_id == ws.id,
    )


def _fetch_badge_keys(
    user_ids: list[int], limit_per_user: int = 3
) -> dict[int, list[str]]:
    """Return up to *limit_per_user* public badge keys per user_id.

    One SQL query; public scope only (workspace_id IS NULL).
    """
    if not user_ids:
        return {}
    rows = db.session.execute(
        select(UserBadge.user_id, Badge.key, UserBadge.awarded_at)
        .join(Badge, Badge.id == UserBadge.badge_id)
        .where(
            UserBadge.user_id.in_(user_ids),
            UserBadge.workspace_id.is_(None),
        )
        .order_by(UserBadge.user_id, UserBadge.awarded_at.desc())
    ).all()

    result: dict[int, list[str]] = {}
    for uid, key, _ in rows:
        bucket = result.setdefault(uid, [])
        if len(bucket) < limit_per_user:
            bucket.append(key)
    return result


# ---------------------------------------------------------------------------
# Core metrics aggregator (4 SQL queries)
# ---------------------------------------------------------------------------


def _compute_candidate_metrics(
    user_ids: list[int],
    workspace=None,
    *,
    ontology_node_ids: list[int] | None = None,
    prompt_family_ids: list[int] | None = None,
) -> dict[int, dict]:
    """Aggregate all raw metrics for *user_ids* using 4 bounded SQL queries.

    Parameters
    ----------
    user_ids:
        Candidate user IDs to evaluate.
    workspace:
        If provided, include public + workspace contributions.
    ontology_node_ids:
        When set, restrict ontology breadth to these nodes only.
    prompt_family_ids:
        When set, restrict accepted-revision count to these post IDs only.

    Returns
    -------
    dict[user_id → {accepted_revisions, benchmark_improvements, ab_wins,
                    ontology_breadth, username, display_name, avatar_url}]
    """
    if not user_ids:
        return {}

    # ── Q1: Accepted revisions per user ─────────────────────────────────────
    rev_stmt = (
        select(Revision.author_id, func.count(Revision.id).label("cnt"))
        .join(Post, Post.id == Revision.post_id)
        .where(
            Revision.author_id.in_(user_ids),
            Revision.status == RevisionStatus.accepted.value,
            _scope_filter_post(workspace),
        )
        .group_by(Revision.author_id)
    )
    if prompt_family_ids is not None:
        rev_stmt = rev_stmt.where(Revision.post_id.in_(prompt_family_ids))

    rev_counts: dict[int, int] = {
        uid: cnt for uid, cnt in db.session.execute(rev_stmt).all()
    }

    # ── Q2: Reputation events (ab_win, benchmark_improvement) per user ──────
    evt_stmt = (
        select(
            ReputationEvent.user_id,
            ReputationEvent.event_type,
            func.count(ReputationEvent.id).label("cnt"),
        )
        .where(
            ReputationEvent.user_id.in_(user_ids),
            ReputationEvent.event_type.in_(["ab_win", "benchmark_improvement"]),
            _scope_filter_re(workspace),
        )
        .group_by(ReputationEvent.user_id, ReputationEvent.event_type)
    )
    ab_wins_counts: dict[int, int] = {}
    bench_counts: dict[int, int] = {}
    for uid, event_type, cnt in db.session.execute(evt_stmt).all():
        if event_type == "ab_win":
            ab_wins_counts[uid] = cnt
        else:
            bench_counts[uid] = cnt

    # ── Q3: Ontology breadth per user ────────────────────────────────────────
    breadth_stmt = (
        select(
            Post.author_id,
            func.count(func.distinct(ContentOntology.ontology_node_id)).label(
                "breadth"
            ),
        )
        .join(Post, Post.id == ContentOntology.post_id)
        .where(
            Post.author_id.in_(user_ids),
            Post.status == PostStatus.published.value,
            _scope_filter_post(workspace),
            _scope_filter_co(workspace),
        )
        .group_by(Post.author_id)
    )
    if ontology_node_ids is not None:
        breadth_stmt = breadth_stmt.where(
            ContentOntology.ontology_node_id.in_(ontology_node_ids)
        )

    breadth_counts: dict[int, int] = {
        uid: breadth for uid, breadth in db.session.execute(breadth_stmt).all()
    }

    # ── Q4: User identity ───────────────────────────────────────────────────
    user_rows = db.session.execute(
        select(User.id, User.username, User.display_name, User.avatar_url).where(
            User.id.in_(user_ids)
        )
    ).all()

    metrics: dict[int, dict] = {}
    for uid, username, display_name, avatar_url in user_rows:
        metrics[uid] = {
            "username": username,
            "display_name": display_name,
            "avatar_url": avatar_url,
            "accepted_revisions": rev_counts.get(uid, 0),
            "benchmark_improvements": bench_counts.get(uid, 0),
            "ab_wins": ab_wins_counts.get(uid, 0),
            "ontology_breadth": breadth_counts.get(uid, 0),
        }

    return metrics


# ---------------------------------------------------------------------------
# Score builder
# ---------------------------------------------------------------------------


def _build_ranked_cards(
    metrics: dict[int, dict],
    limit: int,
    *,
    include_badges: bool = True,
) -> list[ContributorCard]:
    """Normalise, score, sort, optionally attach badges, return top-N cards."""
    if not metrics:
        return []

    uid_list = list(metrics.keys())

    rev_raw = {uid: float(m["accepted_revisions"]) for uid, m in metrics.items()}
    bench_raw = {uid: float(m["benchmark_improvements"]) for uid, m in metrics.items()}
    ab_raw = {uid: float(m["ab_wins"]) for uid, m in metrics.items()}
    ont_raw = {uid: float(m["ontology_breadth"]) for uid, m in metrics.items()}

    norm_rev = _normalize(rev_raw)
    norm_bench = _normalize(bench_raw)
    norm_ab = _normalize(ab_raw)
    norm_ont = _normalize(ont_raw)

    scored: list[tuple[float, int]] = []
    for uid in uid_list:
        score = (
            _W_REVISIONS * norm_rev.get(uid, 0.0)
            + _W_BENCHMARKS * norm_bench.get(uid, 0.0)
            + _W_AB_WINS * norm_ab.get(uid, 0.0)
            + _W_ONTOLOGY * norm_ont.get(uid, 0.0)
        )
        scored.append((score, uid))

    # Deterministic: improver_score DESC, user_id DESC (stable under ties)
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    top = scored[:limit]

    # Optional: Q5 badge keys (one query)
    badge_map: dict[int, list[str]] = {}
    if include_badges:
        badge_map = _fetch_badge_keys([uid for _, uid in top])

    cards: list[ContributorCard] = []
    for rank_idx, (score, uid) in enumerate(top, start=1):
        m = metrics[uid]
        cards.append(
            ContributorCard(
                user_id=uid,
                username=m["username"],
                display_name=m["display_name"],
                avatar_url=m["avatar_url"],
                improver_score=round(score, 4),
                accepted_revisions=m["accepted_revisions"],
                benchmark_improvements=m["benchmark_improvements"],
                ab_wins=m["ab_wins"],
                ontology_breadth=m["ontology_breadth"],
                badge_keys=badge_map.get(uid, []),
                rank=rank_idx,
            )
        )
    return cards


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ContributorCardService:
    """Static-method service for top-improver ranking."""

    # ── Global ────────────────────────────────────────────────────────────────

    @staticmethod
    def get_top_improvers_global(limit: int = 10) -> list[ContributorCard]:
        """Return top contributors by public improver score.

        Candidates are all users with at least one public accepted revision.

        Query budget: 6 (1 candidates + 4 metrics + 1 badges).
        """
        # Q1: Candidates — users with ≥1 accepted public revision
        cand_rows = (
            db.session.execute(
                select(Revision.author_id)
                .distinct()
                .join(Post, Post.id == Revision.post_id)
                .where(
                    Revision.status == RevisionStatus.accepted.value,
                    Post.workspace_id.is_(None),
                )
                .limit(_MAX_CANDIDATES)
            )
            .scalars()
            .all()
        )

        if not cand_rows:
            return []

        metrics = _compute_candidate_metrics(list(cand_rows), workspace=None)
        return _build_ranked_cards(metrics, limit)

    # ── Ontology ──────────────────────────────────────────────────────────────

    @staticmethod
    def get_top_improvers_for_ontology(
        node,
        workspace=None,
        limit: int = 10,
    ) -> list[ContributorCard]:
        """Return top contributors for *node* and its descendants.

        Query budget: 7 (1 descendants + 1 candidates + 4 metrics + 1 badges).
        """
        from backend.services import ontology_service  # noqa: PLC0415

        is_public = workspace is None

        # Q1: All descendant node IDs (BFS — 1 query in ontology_service)
        node_ids = ontology_service.get_all_descendant_ids(
            node.id, public_only=is_public
        )
        if not node_ids:
            return []

        # Q2: Distinct authors of published posts mapped to these nodes (in scope)
        cand_stmt = (
            select(Post.author_id, Post.id.label("post_id"))
            .join(ContentOntology, ContentOntology.post_id == Post.id)
            .where(
                ContentOntology.ontology_node_id.in_(node_ids),
                Post.status == PostStatus.published.value,
                _scope_filter_post(workspace),
                _scope_filter_co(workspace),
            )
            .distinct()
            .limit(_MAX_CANDIDATES)
        )
        cand_result = db.session.execute(cand_stmt).all()
        if not cand_result:
            return []

        user_ids = list({row.author_id for row in cand_result})
        context_post_ids = list({row.post_id for row in cand_result})

        # Q3-Q6: Metrics (scoped to ontology area)
        metrics = _compute_candidate_metrics(
            user_ids,
            workspace=workspace,
            ontology_node_ids=node_ids,
            prompt_family_ids=context_post_ids,
        )
        # Q7: Badges
        return _build_ranked_cards(metrics, limit)

    # ── Prompt family ─────────────────────────────────────────────────────────

    @staticmethod
    def get_top_improvers_for_prompt(
        prompt_post,
        workspace=None,
        limit: int = 10,
    ) -> list[ContributorCard]:
        """Return top revision contributors for *prompt_post* and its forks.

        Fork family = origin + all forks with a ``derived_from`` ContentLink.

        Query budget: 7 (1 family + 1 candidates + 4 metrics + 1 badges).
        """
        # Q1: Discover fork family (origin + forks via ContentLink)
        cl_scope = (
            ContentLink.workspace_id.is_(None)
            if workspace is None
            else or_(
                ContentLink.workspace_id.is_(None),
                ContentLink.workspace_id == workspace.id,
            )
        )

        fork_ids_stmt = (
            select(Post.id)
            .join(ContentLink, ContentLink.from_post_id == Post.id)
            .where(
                ContentLink.to_post_id == prompt_post.id,
                ContentLink.link_type == "derived_from",
                cl_scope,
                Post.status == PostStatus.published.value,
                _scope_filter_post(workspace),
            )
            .limit(_MAX_CANDIDATES)
        )
        fork_rows = db.session.scalars(fork_ids_stmt).all()
        family_ids: list[int] = [prompt_post.id, *fork_rows]

        # Q2: Distinct revision authors on family posts (accepted, in scope)
        cand_stmt = (
            select(Revision.author_id)
            .distinct()
            .join(Post, Post.id == Revision.post_id)
            .where(
                Revision.post_id.in_(family_ids),
                Revision.status == RevisionStatus.accepted.value,
                _scope_filter_post(workspace),
            )
            .limit(_MAX_CANDIDATES)
        )
        user_ids = list(db.session.scalars(cand_stmt).all())
        if not user_ids:
            return []

        # Q3-Q6: Metrics (scoped to prompt family)
        metrics = _compute_candidate_metrics(
            user_ids,
            workspace=workspace,
            prompt_family_ids=family_ids,
        )
        # Q7: Badges
        return _build_ranked_cards(metrics, limit)
