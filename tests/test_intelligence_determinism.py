"""Tests for Intelligence Dashboard deterministic ordering.

Coverage
--------
  ID-001  top_prompts tie-break: equal score → higher post_id ranks first.
  ID-002  most_improved tie-break: equal delta → higher post_id ranks first.
  ID-003  ontology_performance: higher avg_score node ranks first.
  ID-004  fork_outperformance: higher delta fork ranks first.
  ID-005  Time boundary: run exactly within 30-day window IS included.
  ID-006  Run older than 30 days is NOT included in top_prompts.
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

_ctr = itertools.count(5000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"id{n}@example.com",
        username=f"iduser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author):
    n = _n()
    p = Post(
        title=f"ID-Prompt {n}",
        slug=f"id-prompt-{n}",
        kind="prompt",
        markdown_body="hello",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=None,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_run(post, score: float, dt):
    n = _n()
    suite = BenchmarkSuite(
        name=f"ID-Suite {n}",
        slug=f"id-suite-{n}",
        workspace_id=None,
        created_by_user_id=post.author_id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(suite)
    _db.session.flush()

    case_ = BenchmarkCase(
        suite_id=suite.id,
        name=f"ID-Case {n}",
        input_json={},
        created_at=datetime.now(UTC),
    )
    _db.session.add(case_)
    _db.session.flush()

    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=post.id,
        prompt_version=1,
        workspace_id=None,
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


# ── ID-001 ─────────────────────────────────────────────────────────────────────


class TestTopPromptsPostIdTieBreak:
    def test_equal_score_higher_post_id_first(self, db_session):
        user = _make_user()
        p_low_id = _make_prompt(user)
        p_high_id = _make_prompt(user)
        # p_high_id is inserted later → higher id
        assert p_high_id.id > p_low_id.id

        now = datetime.now(UTC)
        dt = now - timedelta(days=5)
        _make_run(p_low_id, 0.70, dt)
        _make_run(p_high_id, 0.70, dt)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=None)
        slugs = [r.slug for r in rows]
        assert p_high_id.slug in slugs
        assert p_low_id.slug in slugs
        assert slugs.index(p_high_id.slug) < slugs.index(p_low_id.slug)


# ── ID-002 ─────────────────────────────────────────────────────────────────────


class TestMostImprovedPostIdTieBreak:
    def test_equal_delta_higher_post_id_first(self, db_session):
        user = _make_user()
        p_low_id = _make_prompt(user)
        p_high_id = _make_prompt(user)
        assert p_high_id.id > p_low_id.id

        now = datetime.now(UTC)
        curr = now - timedelta(days=5)
        prev = now - timedelta(days=45)
        # Both have identical delta = 0.20
        for p in (p_low_id, p_high_id):
            _make_run(p, 0.80, curr)
            _make_run(p, 0.60, prev)
        _db.session.commit()

        rows = intel_svc.get_most_improved(workspace=None)
        slugs = [r.slug for r in rows]
        assert p_high_id.slug in slugs
        assert p_low_id.slug in slugs
        assert slugs.index(p_high_id.slug) < slugs.index(p_low_id.slug)


# ── ID-003 ─────────────────────────────────────────────────────────────────────


class TestOntologyPerformanceOrdering:
    def test_higher_avg_score_node_ranks_first(self, db_session):
        user = _make_user()

        node_low = OntologyNode(
            slug=f"id-node-low-{_n()}",
            name="ID Low Node",
            created_by_user_id=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        node_high = OntologyNode(
            slug=f"id-node-high-{_n()}",
            name="ID High Node",
            created_by_user_id=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add_all([node_low, node_high])
        _db.session.flush()

        p_low = _make_prompt(user)
        p_high = _make_prompt(user)

        dt = datetime.now(UTC) - timedelta(days=5)
        _make_run(p_low, 0.40, dt)
        _make_run(p_high, 0.90, dt)

        for p, node in ((p_low, node_low), (p_high, node_high)):
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
        _db.session.commit()

        rows = intel_svc.get_ontology_performance(workspace=None)
        names = [r.node_name for r in rows]
        assert "ID High Node" in names
        assert "ID Low Node" in names
        assert names.index("ID High Node") < names.index("ID Low Node")


# ── ID-004 ─────────────────────────────────────────────────────────────────────


class TestForkOutperformanceOrdering:
    def test_higher_delta_fork_ranks_first(self, db_session):
        user = _make_user()

        # Fork A: delta = 0.40 (0.80 vs 0.40)
        origin_a = _make_prompt(user)
        fork_a = _make_prompt(user)
        _db.session.add(
            ContentLink(
                from_post_id=fork_a.id,
                to_post_id=origin_a.id,
                link_type="derived_from",
                created_by_user_id=user.id,
            )
        )
        _db.session.flush()

        # Fork B: delta = 0.10 (0.65 vs 0.55)
        origin_b = _make_prompt(user)
        fork_b = _make_prompt(user)
        _db.session.add(
            ContentLink(
                from_post_id=fork_b.id,
                to_post_id=origin_b.id,
                link_type="derived_from",
                created_by_user_id=user.id,
            )
        )
        _db.session.flush()

        dt = datetime.now(UTC) - timedelta(days=5)
        _make_run(fork_a, 0.80, dt)
        _make_run(origin_a, 0.40, dt)
        _make_run(fork_b, 0.65, dt)
        _make_run(origin_b, 0.55, dt)
        _db.session.commit()

        rows = intel_svc.get_fork_outperformance(workspace=None)
        fork_slugs = [r.fork_slug for r in rows]
        assert fork_a.slug in fork_slugs
        assert fork_b.slug in fork_slugs
        assert fork_slugs.index(fork_a.slug) < fork_slugs.index(fork_b.slug)


# ── ID-005 ─────────────────────────────────────────────────────────────────────


class TestTimeBoundaryIncluded:
    def test_run_just_inside_30d_window_included(self, db_session):
        user = _make_user()
        p = _make_prompt(user)

        # Exactly 29 days + 23 hours ago → well within the 30-day window
        just_inside = datetime.now(UTC) - timedelta(days=29, hours=23)
        _make_run(p, 0.75, just_inside)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=None)
        slugs = [r.slug for r in rows]
        assert p.slug in slugs


# ── ID-006 ─────────────────────────────────────────────────────────────────────


class TestTimeBoundaryExcluded:
    def test_run_older_than_30d_not_in_top_prompts(self, db_session):
        user = _make_user()
        p = _make_prompt(user)

        # 31 days ago → outside the 30-day window
        too_old = datetime.now(UTC) - timedelta(days=31)
        _make_run(p, 0.95, too_old)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=None)
        slugs = [r.slug for r in rows]
        assert p.slug not in slugs
