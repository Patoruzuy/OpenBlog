"""User Contribution Analytics Service.

Provides four functions that power the profile contribution analytics panel.
All functions are scope-aware via the *public_only* flag:

  public_only=True  → workspace_id IS NULL (public contributions only)
  public_only=False → all contributions regardless of workspace (owner view)

Query budget per full page render
----------------------------------
1. build_contribution_heatmap   → 1 UNION ALL (5 sources, 365-day window)
2. build_user_contribution_summary → 1 SELECT with 6 scalar subqueries
3. build_ontology_contributions → 1 JOIN + GROUP BY + LIMIT
4. compute_contribution_streak  → 1 UNION ALL (5 sources, full history)
                                    ─────────────────────────────────
Total                           = 4 DB round-trips  (≤ 8 budget)

Scope enforcement
-----------------
Public view  (public_only=True ): workspace_id IS NULL filter applied to every
             sub-query.  Revisions are scoped via a JOIN to their parent Post.
Owner view   (public_only=False): no workspace filter — the authenticated owner
             sees all their contributions.
Never leaks workspace data to non-owners.  Enforcement is SQL-level only;
no Python post-filtering.

Level thresholds (fixed, GitHub-style)
---------------------------------------
0 = 0 contributions
1 = 1
2 = 2–3
3 = 4–6
4 = 7+

Streak algorithm
----------------
``compute_contribution_streak`` fetches every distinct contribution date over
the user's full activity history via a UNION ALL.  It then:

  1. Sorts dates ascending and walks forward to find the *longest* consecutive
     run (each day exactly one calendar day after its predecessor).
  2. Walks backward from today (or yesterday when today has no contribution)
     to find the *current* streak — stopping at the first gap.

Both calculations are O(n) over the distinct date count.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, union_all

from backend.extensions import db
from backend.models.ab_experiment import ABExperiment
from backend.models.ai_review import AIReviewRequest
from backend.models.benchmark import BenchmarkRun
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus

if TYPE_CHECKING:
    pass

_WEEKS = 52


# ── Level helper ──────────────────────────────────────────────────────────────


def _level(count: int) -> int:
    """Map a raw contribution count to a display level (0–4)."""
    if count == 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 6:
        return 3
    return 4


# ── Shared UNION ALL date aggregation ─────────────────────────────────────────


def _union_date_counts(
    user_id: int,
    *,
    public_only: bool,
    start_dt: datetime.datetime | None = None,
    end_dt: datetime.datetime | None = None,
) -> dict[datetime.date, int]:
    """Internal helper: single UNION ALL → date → total contribution count.

    Sources counted
    ---------------
    - Published posts      (Post.published_at)
    - Accepted revisions   (Revision.reviewed_at, scoped via Post join)
    - AI review requests   (AIReviewRequest.created_at)
    - Benchmark runs       (BenchmarkRun.created_at)
    - A/B experiments      (ABExperiment.created_at)

    ``func.date(col)`` extracts the calendar date in SQLite (returns
    "YYYY-MM-DD" string) and PostgreSQL (returns a date object).
    Both forms are handled in the post-processing step.
    """
    # ── Published posts ────────────────────────────────────────────────────
    q_posts = select(func.date(Post.published_at).label("d")).where(
        Post.author_id == user_id,
        Post.status == PostStatus.published.value,
        Post.published_at.isnot(None),
    )
    if public_only:
        q_posts = q_posts.where(Post.workspace_id.is_(None))
    if start_dt is not None:
        q_posts = q_posts.where(Post.published_at >= start_dt)
    if end_dt is not None:
        q_posts = q_posts.where(Post.published_at <= end_dt)

    # ── Accepted revisions (scope enforced via Post join) ─────────────────
    q_rev = (
        select(func.date(Revision.reviewed_at).label("d"))
        .join(Post, Revision.post_id == Post.id)
        .where(
            Revision.author_id == user_id,
            Revision.status == RevisionStatus.accepted.value,
            Revision.reviewed_at.isnot(None),
        )
    )
    if public_only:
        q_rev = q_rev.where(Post.workspace_id.is_(None))
    if start_dt is not None:
        q_rev = q_rev.where(Revision.reviewed_at >= start_dt)
    if end_dt is not None:
        q_rev = q_rev.where(Revision.reviewed_at <= end_dt)

    # ── AI review requests ─────────────────────────────────────────────────
    q_ai = select(func.date(AIReviewRequest.created_at).label("d")).where(
        AIReviewRequest.requested_by_user_id == user_id,
    )
    if public_only:
        q_ai = q_ai.where(AIReviewRequest.workspace_id.is_(None))
    if start_dt is not None:
        q_ai = q_ai.where(AIReviewRequest.created_at >= start_dt)
    if end_dt is not None:
        q_ai = q_ai.where(AIReviewRequest.created_at <= end_dt)

    # ── Benchmark runs ─────────────────────────────────────────────────────
    q_bench = select(func.date(BenchmarkRun.created_at).label("d")).where(
        BenchmarkRun.created_by_user_id == user_id,
    )
    if public_only:
        q_bench = q_bench.where(BenchmarkRun.workspace_id.is_(None))
    if start_dt is not None:
        q_bench = q_bench.where(BenchmarkRun.created_at >= start_dt)
    if end_dt is not None:
        q_bench = q_bench.where(BenchmarkRun.created_at <= end_dt)

    # ── A/B experiments ────────────────────────────────────────────────────
    q_ab = select(func.date(ABExperiment.created_at).label("d")).where(
        ABExperiment.created_by_user_id == user_id,
    )
    if public_only:
        q_ab = q_ab.where(ABExperiment.workspace_id.is_(None))
    if start_dt is not None:
        q_ab = q_ab.where(ABExperiment.created_at >= start_dt)
    if end_dt is not None:
        q_ab = q_ab.where(ABExperiment.created_at <= end_dt)

    # ── UNION ALL → group by date ──────────────────────────────────────────
    combined = union_all(q_posts, q_rev, q_ai, q_bench, q_ab).subquery("combined")
    stmt = (
        select(combined.c.d, func.count().label("cnt"))
        .where(combined.c.d.isnot(None))
        .group_by(combined.c.d)
        .order_by(combined.c.d)
    )
    rows = db.session.execute(stmt).all()

    result: dict[datetime.date, int] = {}
    for d_raw, cnt in rows:
        if d_raw is None:
            continue
        # SQLite returns a string; PostgreSQL returns a date object.
        if isinstance(d_raw, str):
            d = datetime.date.fromisoformat(d_raw)
        elif isinstance(d_raw, datetime.datetime):
            d = d_raw.date()
        else:
            d = d_raw  # already datetime.date
        result[d] = result.get(d, 0) + cnt
    return result


# ── Public API ────────────────────────────────────────────────────────────────


def build_contribution_heatmap(user_id: int, *, public_only: bool = True) -> dict:
    """Build a 52-week contribution heatmap.

    Returns
    -------
    {
        "weeks": [
            [{"date": "YYYY-MM-DD", "count": int, "level": 0-4}, ...],
            ...
        ],
        "total": int,
    }

    The grid covers exactly 52 × 7 = 364 calendar days ending today.
    Level thresholds are fixed (not relative to the user's max):
      0=none, 1=1 contribution, 2=2-3, 3=4-6, 4=7+
    """
    today = datetime.date.today()
    grid_end = today
    grid_start = (
        grid_end - datetime.timedelta(weeks=_WEEKS) + datetime.timedelta(days=1)
    )

    start_dt = datetime.datetime(
        grid_start.year,
        grid_start.month,
        grid_start.day,
        0,
        0,
        0,
        tzinfo=datetime.UTC,
    )
    end_dt = datetime.datetime(
        grid_end.year,
        grid_end.month,
        grid_end.day,
        23,
        59,
        59,
        tzinfo=datetime.UTC,
    )

    counts = _union_date_counts(
        user_id, public_only=public_only, start_dt=start_dt, end_dt=end_dt
    )

    weeks: list[list[dict]] = []
    current = grid_start
    while current <= grid_end:
        week: list[dict] = []
        for _ in range(7):
            if current > grid_end:
                break
            c = counts.get(current, 0)
            week.append({"date": current.isoformat(), "count": c, "level": _level(c)})
            current += datetime.timedelta(days=1)
        if week:
            weeks.append(week)

    total = sum(counts.values())
    return {"weeks": weeks, "total": total}


def build_user_contribution_summary(user_id: int, *, public_only: bool = True) -> dict:
    """Aggregate contribution counts from all sources.

    Returns
    -------
    {
        "posts_published": int,
        "revisions_submitted": int,
        "revisions_accepted": int,
        "ai_reviews_requested": int,
        "benchmarks_run": int,
        "ab_experiments_created": int,
    }

    Uses a single SELECT with scalar subqueries (1 DB round-trip).
    """
    # ── Posts published ────────────────────────────────────────────────────
    pp_q = select(func.count(Post.id)).where(
        Post.author_id == user_id,
        Post.status == PostStatus.published.value,
    )
    if public_only:
        pp_q = pp_q.where(Post.workspace_id.is_(None))

    # ── Revisions submitted ────────────────────────────────────────────────
    if public_only:
        rs_q = (
            select(func.count(Revision.id))
            .join(Post, Revision.post_id == Post.id)
            .where(Revision.author_id == user_id, Post.workspace_id.is_(None))
        )
    else:
        rs_q = select(func.count(Revision.id)).where(Revision.author_id == user_id)

    # ── Revisions accepted ─────────────────────────────────────────────────
    if public_only:
        ra_q = (
            select(func.count(Revision.id))
            .join(Post, Revision.post_id == Post.id)
            .where(
                Revision.author_id == user_id,
                Revision.status == RevisionStatus.accepted.value,
                Post.workspace_id.is_(None),
            )
        )
    else:
        ra_q = select(func.count(Revision.id)).where(
            Revision.author_id == user_id,
            Revision.status == RevisionStatus.accepted.value,
        )

    # ── AI review requests ─────────────────────────────────────────────────
    ai_q = select(func.count(AIReviewRequest.id)).where(
        AIReviewRequest.requested_by_user_id == user_id,
    )
    if public_only:
        ai_q = ai_q.where(AIReviewRequest.workspace_id.is_(None))

    # ── Benchmark runs ─────────────────────────────────────────────────────
    bk_q = select(func.count(BenchmarkRun.id)).where(
        BenchmarkRun.created_by_user_id == user_id,
    )
    if public_only:
        bk_q = bk_q.where(BenchmarkRun.workspace_id.is_(None))

    # ── A/B experiments ────────────────────────────────────────────────────
    ab_q = select(func.count(ABExperiment.id)).where(
        ABExperiment.created_by_user_id == user_id,
    )
    if public_only:
        ab_q = ab_q.where(ABExperiment.workspace_id.is_(None))

    row = db.session.execute(
        select(
            pp_q.scalar_subquery().label("pp"),
            rs_q.scalar_subquery().label("rs"),
            ra_q.scalar_subquery().label("ra"),
            ai_q.scalar_subquery().label("ai"),
            bk_q.scalar_subquery().label("bk"),
            ab_q.scalar_subquery().label("ab"),
        )
    ).one()

    return {
        "posts_published": row.pp or 0,
        "revisions_submitted": row.rs or 0,
        "revisions_accepted": row.ra or 0,
        "ai_reviews_requested": row.ai or 0,
        "benchmarks_run": row.bk or 0,
        "ab_experiments_created": row.ab or 0,
    }


def build_ontology_contributions(
    user_id: int, *, public_only: bool = True, limit: int = 5
) -> list[dict]:
    """Top *limit* ontology nodes the user has contributed to via published posts.

    Returns
    -------
    [{"node": OntologyNode, "count": int}, ...]  ordered by count desc,
    then OntologyNode.id desc for determinism.

    Scope: public_only restricts to public posts (workspace_id IS NULL) and
    public content-ontology mappings (workspace_id IS NULL).
    """
    stmt = (
        select(OntologyNode, func.count(ContentOntology.id).label("cnt"))
        .join(ContentOntology, ContentOntology.ontology_node_id == OntologyNode.id)
        .join(Post, ContentOntology.post_id == Post.id)
        .where(
            Post.author_id == user_id,
            Post.status == PostStatus.published.value,
        )
        .group_by(OntologyNode.id)
        .order_by(func.count(ContentOntology.id).desc(), OntologyNode.id.desc())
        .limit(limit)
    )
    if public_only:
        stmt = stmt.where(
            Post.workspace_id.is_(None),
            ContentOntology.workspace_id.is_(None),
        )

    rows = db.session.execute(stmt).all()
    return [{"node": node, "count": cnt} for node, cnt in rows]


def compute_contribution_streak(user_id: int, *, public_only: bool = True) -> dict:
    """Compute current and longest contribution streaks.

    Streak definition: a streak increments for each consecutive calendar day
    on which *any* contribution occurred (across all 5 sources).

    Algorithm
    ---------
    1. Fetch all distinct contribution dates (full history, no date window)
       via a UNION ALL query over all 5 sources.
    2. Sort dates ascending.
    3. Walk forward to find *longest_streak*: scan for maximal consecutive
       runs where each date is exactly the predecessor + 1 day.
    4. Walk backward from today for *current_streak*: start from today (or
       yesterday if today has no contribution) and count consecutive days
       until the first gap.

    Returns
    -------
    {"current_streak": int, "longest_streak": int}
    """
    date_counts = _union_date_counts(user_id, public_only=public_only)
    sorted_dates = sorted(date_counts.keys())

    if not sorted_dates:
        return {"current_streak": 0, "longest_streak": 0}

    # ── Longest streak ─────────────────────────────────────────────────────
    longest = 1
    run = 1
    for i in range(1, len(sorted_dates)):
        if sorted_dates[i] == sorted_dates[i - 1] + datetime.timedelta(days=1):
            run += 1
            if run > longest:
                longest = run
        else:
            run = 1

    # ── Current streak (from today backwards) ─────────────────────────────
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    date_set = frozenset(sorted_dates)

    current = 0
    if today in date_set or yesterday in date_set:
        check = today if today in date_set else yesterday
        while check in date_set:
            current += 1
            check -= datetime.timedelta(days=1)

    return {"current_streak": current, "longest_streak": longest}
