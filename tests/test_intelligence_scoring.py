"""Tests for Intelligence Dashboard service scoring math.

Coverage
--------
  IS-001  top_prompts: higher avg score ranks first.
  IS-002  most_improved: prompt with delta > 0 appears in results.
  IS-003  most_improved: prompt only in current window is excluded.
  IS-004  most_improved: prompt with delta ≤ 0 excluded.
  IS-005  ontology_performance: correct avg_score and prompt_count.
  IS-006  fork_outperformance: delta equals fork_score − origin_score.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.content_link import ContentLink
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.services import intelligence_service as intel_svc

_ctr = itertools.count(3000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"is{n}@example.com",
        username=f"isuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id=None):
    n = _n()
    p = Post(
        title=f"IS-Prompt {n}",
        slug=f"is-prompt-{n}",
        kind="prompt",
        markdown_body="hello",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_run(post, score: float, dt, workspace_id=None):
    """Create a completed BenchmarkRun + Result with the given score at datetime *dt*."""
    n = _n()
    suite = BenchmarkSuite(
        name=f"IS-Suite {n}",
        slug=f"is-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(suite)
    _db.session.flush()

    case_ = BenchmarkCase(
        suite_id=suite.id,
        name=f"IS-Case {n}",
        input_json={},
        created_at=datetime.now(UTC),
    )
    _db.session.add(case_)
    _db.session.flush()

    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=post.id,
        prompt_version=1,
        workspace_id=workspace_id,
        model_name="test-model",
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=post.author_id,
        created_at=dt,
        completed_at=dt,
    )
    _db.session.add(run)
    _db.session.flush()

    result = BenchmarkRunResult(
        run_id=run.id,
        case_id=case_.id,
        output_text="ok",
        score_numeric=score,
        created_at=dt,
    )
    _db.session.add(result)
    _db.session.flush()
    return run, result


# ── IS-001 ─────────────────────────────────────────────────────────────────────


class TestTopPromptRanking:
    def test_higher_score_ranks_first(self, db_session):
        user = _make_user()
        p_low = _make_prompt(user)
        p_high = _make_prompt(user)

        now = datetime.now(UTC)
        curr = now - timedelta(days=5)
        _make_run(p_low, 0.50, curr)
        _make_run(p_high, 0.90, curr)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=None)
        slugs = [r.slug for r in rows]
        assert p_high.slug in slugs
        assert p_low.slug in slugs
        assert slugs.index(p_high.slug) < slugs.index(p_low.slug)


# ── IS-002 ─────────────────────────────────────────────────────────────────────


class TestMostImprovedPositiveDelta:
    def test_improving_prompt_appears(self, db_session):
        user = _make_user()
        p = _make_prompt(user)

        now = datetime.now(UTC)
        curr_dt = now - timedelta(days=5)
        prev_dt = now - timedelta(days=45)
        _make_run(p, 0.80, curr_dt)
        _make_run(p, 0.50, prev_dt)
        _db.session.commit()

        rows = intel_svc.get_most_improved(workspace=None)
        slugs = [r.slug for r in rows]
        assert p.slug in slugs


# ── IS-003 ─────────────────────────────────────────────────────────────────────


class TestMostImprovedCurrentOnlyExcluded:
    def test_prompt_only_in_current_window_excluded(self, db_session):
        user = _make_user()
        p = _make_prompt(user)

        now = datetime.now(UTC)
        curr_dt = now - timedelta(days=5)
        # Only current window, no previous window data
        _make_run(p, 0.80, curr_dt)
        _db.session.commit()

        rows = intel_svc.get_most_improved(workspace=None)
        slugs = [r.slug for r in rows]
        assert p.slug not in slugs


# ── IS-004 ─────────────────────────────────────────────────────────────────────


class TestMostImprovedNegativeDeltaExcluded:
    def test_worse_or_equal_score_excluded(self, db_session):
        user = _make_user()
        p_worse = _make_prompt(user)
        p_equal = _make_prompt(user)

        now = datetime.now(UTC)
        curr_dt = now - timedelta(days=5)
        prev_dt = now - timedelta(days=45)

        # p_worse: current (0.40) < previous (0.70) → delta < 0
        _make_run(p_worse, 0.40, curr_dt)
        _make_run(p_worse, 0.70, prev_dt)

        # p_equal: current == previous → delta == 0
        _make_run(p_equal, 0.60, curr_dt)
        _make_run(p_equal, 0.60, prev_dt)
        _db.session.commit()

        rows = intel_svc.get_most_improved(workspace=None)
        slugs = [r.slug for r in rows]
        assert p_worse.slug not in slugs
        assert p_equal.slug not in slugs


# ── IS-005 ─────────────────────────────────────────────────────────────────────


class TestOntologyAvgAndCount:
    def test_correct_avg_score_and_prompt_count(self, db_session):
        user = _make_user()
        p1 = _make_prompt(user)
        p2 = _make_prompt(user)

        node = OntologyNode(
            slug=f"is-node-{_n()}",
            name="IS Node",
            created_by_user_id=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(node)
        _db.session.flush()

        for p in (p1, p2):
            _db.session.add(
                ContentOntology(
                    post_id=p.id,
                    ontology_node_id=node.id,
                    workspace_id=None,
                    created_by_user_id=user.id,
                    created_at=datetime.now(UTC),
                )
            )
        _db.session.flush()

        now = datetime.now(UTC)
        curr_dt = now - timedelta(days=5)
        _make_run(p1, 0.60, curr_dt)
        _make_run(p2, 0.80, curr_dt)
        _db.session.commit()

        rows = intel_svc.get_ontology_performance(workspace=None)
        node_rows = [r for r in rows if r.node_name == "IS Node"]
        assert len(node_rows) == 1
        row = node_rows[0]
        assert row.prompt_count == 2
        assert abs(row.avg_score - 0.70) < 1e-6


# ── IS-006 ─────────────────────────────────────────────────────────────────────


class TestForkOutperformanceDelta:
    def test_delta_equals_fork_minus_origin(self, db_session):
        user = _make_user()
        origin = _make_prompt(user)
        fork = _make_prompt(user)

        link = ContentLink(
            from_post_id=fork.id,
            to_post_id=origin.id,
            link_type="derived_from",
            created_by_user_id=user.id,
        )
        _db.session.add(link)
        _db.session.flush()

        now = datetime.now(UTC)
        curr_dt = now - timedelta(days=5)
        fork_score = 0.85
        origin_score = 0.60
        _make_run(fork, fork_score, curr_dt)
        _make_run(origin, origin_score, curr_dt)
        _db.session.commit()

        rows = intel_svc.get_fork_outperformance(workspace=None)
        assert rows, "Expected at least one fork outperformance row"
        row = rows[0]
        assert row.fork_slug == fork.slug
        assert row.origin_slug == origin.slug
        assert abs(row.fork_score - fork_score) < 1e-6
        assert abs(row.origin_score - origin_score) < 1e-6
        assert abs(row.delta - (fork_score - origin_score)) < 1e-6
