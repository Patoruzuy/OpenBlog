"""Tests for Fork Recommendation Service — scoring formula and ordering.

Coverage
--------
  FRS-001  Fork with higher benchmark avg ranks first.
  FRS-002  Fork with no benchmark runs still scores from ratings/recency.
  FRS-003  More votes → higher rating contribution.
  FRS-004  More views → higher execution contribution.
  FRS-005  Normalization: when max_votes == 0, all rating_contribs are 0.0.
  FRS-006  Single-fork family: score equals weighted sum of its own dimensions.
  FRS-007  Tie-break by version: equal score → higher version wins.
  FRS-008  Tie-break by updated_at: equal score & version → newer updated_at wins.
  FRS-009  Deterministic: calling recommend() twice returns identical order.
  FRS-010  build_breakdown() passthrough returns same breakdown object.
  FRS-011  Score components sum to the overall score (within float tolerance).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from backend.extensions import db as _db
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.vote import Vote
from backend.services import fork_recommendation_service as svc

_ctr = itertools.count(12_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"frs{n}@example.com",
        username=f"frsuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"FRS-Prompt {n}",
        slug=f"frs-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_fork(
    base: Post,
    author,
    *,
    view_count: int = 0,
    version: int = 1,
    updated_at: datetime | None = None,
) -> Post:
    n = _n()
    fork = Post(
        title=f"FRS-Fork {n}",
        slug=f"frs-fork-{n}",
        kind="prompt",
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=None,
        view_count=view_count,
        version=version,
    )
    if updated_at is not None:
        fork.updated_at = updated_at
    _db.session.add(fork)
    _db.session.flush()
    _db.session.add(
        ContentLink(
            from_post_id=fork.id,
            to_post_id=base.id,
            link_type="derived_from",
            created_by_user_id=author.id,
        )
    )
    _db.session.flush()
    return fork


def _add_vote(voter, fork: Post) -> None:
    _db.session.add(Vote(user_id=voter.id, target_type="post", target_id=fork.id))
    _db.session.flush()


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"FRS Suite {n}",
        slug=f"frs-suite-{n}",
        created_by_user_id=user.id,
        workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_case(suite) -> BenchmarkCase:
    n = _n()
    c = BenchmarkCase(
        suite_id=suite.id,
        name=f"FRS Case {n}",
        input_json={"q": "test"},
    )
    _db.session.add(c)
    _db.session.flush()
    return c


def _make_completed_run(suite, fork, user, *, score: float) -> BenchmarkRun:
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=fork.id,
        prompt_version=fork.version,
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=user.id,
    )
    _db.session.add(run)
    _db.session.flush()
    case = _make_case(suite)
    _db.session.add(
        BenchmarkRunResult(
            run_id=run.id,
            case_id=case.id,
            output_text="output",
            score_numeric=score,
        )
    )
    _db.session.flush()
    return run


# ==============================================================================
# FRS-001 — benchmark score drives ranking
# ==============================================================================


class TestBenchmarkScoreRanking:
    def test_higher_benchmark_score_ranks_first(self, db_session):
        """FRS-001"""
        user = _make_user()
        base = _make_prompt(user)
        fork_low = _make_fork(base, user)
        fork_high = _make_fork(base, user)
        suite = _make_suite(user)
        _make_completed_run(suite, fork_low, user, score=0.5)
        _make_completed_run(suite, fork_high, user, score=0.9)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 2
        assert recs[0].post_id == fork_high.id, (
            f"Expected fork_high (score=0.9) first; got {recs[0].post_id}"
        )
        assert recs[1].post_id == fork_low.id


# ==============================================================================
# FRS-002 — fork with no benchmark still scores
# ==============================================================================


class TestNoRunsFork:
    def test_fork_without_benchmark_scores_via_recency(self, db_session):
        """FRS-002: a fork with no benchmark run can still have a non-zero score
        from recency (updated_at is recent by default)."""
        user = _make_user()
        base = _make_prompt(user)
        _fork = _make_fork(base, user)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 1
        bd = recs[0].breakdown
        assert bd.benchmark_raw is None
        assert bd.benchmark_contrib == 0.0
        # Should still have a positive recency contribution (fork was just created)
        assert bd.recency_contrib > 0.0


# ==============================================================================
# FRS-003 — votes drive rating contribution
# ==============================================================================


class TestVoteContribution:
    def test_more_votes_ranks_higher(self, db_session):
        """FRS-003"""
        user = _make_user()
        base = _make_prompt(user)
        fork_few = _make_fork(base, user)
        fork_many = _make_fork(base, user)

        # Give fork_many more votes
        for _ in range(3):
            voter = _make_user()
            _add_vote(voter, fork_many)
        voter = _make_user()
        _add_vote(voter, fork_few)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 2
        positions = {r.post_id: i for i, r in enumerate(recs)}
        assert positions[fork_many.id] < positions[fork_few.id]


# ==============================================================================
# FRS-004 — view_count drives execution contribution
# ==============================================================================


class TestExecutionCountContribution:
    def test_more_views_ranks_higher_when_other_signals_equal(self, db_session):
        """FRS-004"""
        user = _make_user()
        base = _make_prompt(user)
        old_time = datetime.now(UTC) - timedelta(days=30)
        fork_few_views = _make_fork(base, user, view_count=1, updated_at=old_time)
        fork_many_views = _make_fork(base, user, view_count=100, updated_at=old_time)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        positions = {r.post_id: i for i, r in enumerate(recs)}
        # fork_many_views has a higher execution_contrib
        assert recs[positions[fork_many_views.id]].breakdown.execution_contrib > (
            recs[positions[fork_few_views.id]].breakdown.execution_contrib
        )


# ==============================================================================
# FRS-005 — normalization: all zeros → zero contrib
# ==============================================================================


class TestNormalizationAllZeros:
    def test_rating_contrib_zero_when_no_votes(self, db_session):
        """FRS-005"""
        user = _make_user()
        base = _make_prompt(user)
        _make_fork(base, user)
        _make_fork(base, user)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert all(r.breakdown.rating_contrib == 0.0 for r in recs)

    def test_execution_contrib_zero_when_all_zero_views(self, db_session):
        user = _make_user()
        base = _make_prompt(user)
        _make_fork(base, user, view_count=0)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert recs[0].breakdown.execution_contrib == 0.0


# ==============================================================================
# FRS-006 — single fork: score equals its own weighted dimensions
# ==============================================================================


class TestSingleForkScoreFormula:
    def test_single_fork_score_equals_weighted_sum(self, db_session):
        """FRS-006: with a single fork, normalization produces 1.0 for each
        dimension that has data, so result is verifiable."""
        user = _make_user()
        base = _make_prompt(user)
        fork = _make_fork(base, user, view_count=10)
        voter = _make_user()
        _add_vote(voter, fork)
        suite = _make_suite(user)
        _make_completed_run(suite, fork, user, score=0.8)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 1
        bd = recs[0].breakdown

        # With a single fork, all max values == the fork's own values → all norms == 1.0
        assert bd.benchmark_contrib == pytest.approx(0.40, abs=1e-4)
        assert bd.rating_contrib == pytest.approx(0.25, abs=1e-4)
        assert bd.execution_contrib == pytest.approx(0.15, abs=1e-4)
        # ab_contrib == 0.0 because no AB experiments
        assert bd.ab_contrib == 0.0


# ==============================================================================
# FRS-007 — tie-break: higher version wins
# ==============================================================================


class TestTieBreakByVersion:
    def test_higher_version_wins_on_equal_score(self, db_session):
        """FRS-007"""
        user = _make_user()
        base = _make_prompt(user)
        # Both forks will have identical zero signals → tied on score.
        # Use the same updated_at so that only version differs.
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        _fork_v1 = _make_fork(base, user, version=1, updated_at=ts)
        fork_v3 = _make_fork(base, user, version=3, updated_at=ts)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 2
        assert recs[0].post_id == fork_v3.id, "Higher version should rank first on tie"


# ==============================================================================
# FRS-008 — tie-break: newer updated_at wins
# ==============================================================================


class TestTieBreakByUpdatedAt:
    def test_newer_updated_at_wins_on_equal_score_and_version(self, db_session):
        """FRS-008"""
        user = _make_user()
        base = _make_prompt(user)
        old_ts = datetime(2023, 6, 1, tzinfo=UTC)
        new_ts = datetime(2024, 6, 1, tzinfo=UTC)
        _fork_old = _make_fork(base, user, version=1, updated_at=old_ts)
        fork_new = _make_fork(base, user, version=1, updated_at=new_ts)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 2
        assert recs[0].post_id == fork_new.id, "Newer updated_at should rank first"


# ==============================================================================
# FRS-009 — deterministic: two calls produce same order
# ==============================================================================


class TestDeterministic:
    def test_two_calls_return_same_order(self, db_session):
        """FRS-009"""
        user = _make_user()
        base = _make_prompt(user)
        _make_fork(base, user, view_count=5)
        _make_fork(base, user, view_count=10)
        _make_fork(base, user, view_count=1)
        _db.session.commit()

        recs_1 = svc.recommend(user, base, workspace=None)
        recs_2 = svc.recommend(user, base, workspace=None)
        assert [r.post_id for r in recs_1] == [r.post_id for r in recs_2]


# ==============================================================================
# FRS-010 — build_breakdown passthrough
# ==============================================================================


class TestBuildBreakdownPassthrough:
    def test_build_breakdown_returns_same_object(self, db_session):
        """FRS-010"""
        user = _make_user()
        base = _make_prompt(user)
        _make_fork(base, user)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 1
        rec = recs[0]
        assert svc.build_breakdown(rec) is rec.breakdown


# ==============================================================================
# FRS-011 — component sum equals overall score
# ==============================================================================


class TestScoreComponentSum:
    def test_component_contributions_sum_to_score(self, db_session):
        """FRS-011"""
        user = _make_user()
        base = _make_prompt(user)
        fork = _make_fork(base, user, view_count=7)
        _add_vote(_make_user(), fork)
        suite = _make_suite(user)
        _make_completed_run(suite, fork, user, score=0.6)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 1
        bd = recs[0].breakdown

        component_sum = (
            bd.benchmark_contrib
            + bd.rating_contrib
            + bd.execution_contrib
            + bd.recency_contrib
            + bd.ab_contrib
        )
        assert component_sum == pytest.approx(bd.score, abs=1e-4)
