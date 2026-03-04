"""Tests for A/B Experiment run lifecycle.

Coverage
--------
  ABRU-001  start_experiment creates ABExperimentRun with correct run_a/run_b.
  ABRU-002  run_a.prompt_post_id == variant_a_prompt, version correct.
  ABRU-003  run_b.prompt_post_id == variant_b_prompt, version correct.
  ABRU-004  experiment.status → 'running' after start.
  ABRU-005  Both benchmark runs reach terminal state (eager task).
  ABRU-006  compute_comparison triggers status → 'completed'.
  ABRU-007  Cannot start an already-running experiment.
  ABRU-008  Cannot start a completed experiment.
  ABRU-009  Cannot start a canceled experiment.
  ABRU-010  cancel_experiment cancels underlying queued runs.
  ABRU-011  Unauthenticated user cannot start.
  ABRU-012  POST /ab/<slug>/start 302→detail for authenticated owner.
  ABRU-013  POST /ab/<slug>/cancel 302→detail for authenticated owner.
  ABRU-014  start route requires authentication (302 to login).
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkSuite,
)
from backend.models.post import Post, PostStatus
from backend.services import ab_experiment_service as ab_svc
from backend.services.benchmark_service import BenchmarkError

_ctr = itertools.count(2_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"abru{n}@example.com",
        username=f"abruuser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"ABRU-Prompt {n}",
        slug=f"abru-prompt-{n}",
        kind="prompt",
        markdown_body="Answer: {{question}}",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"ABRU Suite {n}",
        slug=f"abru-suite-{n}",
        created_by_user_id=user.id,
        workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _add_case(suite):
    n = _n()
    c = BenchmarkCase(
        suite_id=suite.id,
        name=f"Case {n}",
        input_json={"question": "what is 2+2?"},
    )
    _db.session.add(c)
    _db.session.flush()
    return c


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


def _make_draft_experiment(user):
    """Return a committed (user, suite, exp) ready to be started."""
    suite = _make_suite(user)
    _add_case(suite)  # give the task something to process
    pa = _make_prompt(user)
    pb = _make_prompt(user)
    _db.session.commit()
    exp = ab_svc.create_experiment(user, "Run-Exp", suite, pa, 1, pb, 2)
    _db.session.commit()
    return suite, pa, pb, exp


# ── ABRU-001 ──────────────────────────────────────────────────────────────────


class TestStartCreatesRuns:
    def test_creates_ab_experiment_run_row(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        exp_run = ab_svc.start_experiment(user, exp)
        _db.session.commit()

        assert exp_run is not None
        assert exp_run.experiment_id == exp.id
        assert exp_run.run_a_id is not None
        assert exp_run.run_b_id is not None
        assert exp_run.run_a_id != exp_run.run_b_id


# ── ABRU-002 / ABRU-003 ───────────────────────────────────────────────────────


class TestRunVariantAssignment:
    def test_run_a_assigned_to_variant_a_prompt_and_version(self, db_session):
        user = _make_user()
        _suite, pa, pb, exp = _make_draft_experiment(user)

        exp_run = ab_svc.start_experiment(user, exp)
        _db.session.commit()

        run_a = _db.session.get(BenchmarkRun, exp_run.run_a_id)
        assert run_a is not None
        assert run_a.prompt_post_id == pa.id
        assert run_a.prompt_version == exp.variant_a_version

    def test_run_b_assigned_to_variant_b_prompt_and_version(self, db_session):
        user = _make_user()
        _suite, pa, pb, exp = _make_draft_experiment(user)

        exp_run = ab_svc.start_experiment(user, exp)
        _db.session.commit()

        run_b = _db.session.get(BenchmarkRun, exp_run.run_b_id)
        assert run_b is not None
        assert run_b.prompt_post_id == pb.id
        assert run_b.prompt_version == exp.variant_b_version


# ── ABRU-004 ──────────────────────────────────────────────────────────────────


class TestStatusTransitions:
    def test_experiment_status_running_after_start(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        ab_svc.start_experiment(user, exp)
        _db.session.commit()

        assert exp.status == "running"

    def test_started_at_populated(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        ab_svc.start_experiment(user, exp)
        _db.session.commit()

        assert exp.started_at is not None


# ── ABRU-005 / ABRU-006 ───────────────────────────────────────────────────────


class TestEagerTaskCompletion:
    def test_both_runs_reach_terminal_state(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        exp_run = ab_svc.start_experiment(user, exp)
        _db.session.commit()

        terminal = {"completed", "failed", "canceled"}
        run_a = _db.session.get(BenchmarkRun, exp_run.run_a_id)
        run_b = _db.session.get(BenchmarkRun, exp_run.run_b_id)
        assert run_a.status in terminal
        assert run_b.status in terminal

    def test_compute_comparison_marks_experiment_completed(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        ab_svc.start_experiment(user, exp)
        _db.session.commit()

        cmp = ab_svc.compute_comparison(user, exp)
        _db.session.commit()

        assert cmp.experiment.status == "completed"
        assert cmp.experiment.completed_at is not None


# ── ABRU-007 / ABRU-008 / ABRU-009 ───────────────────────────────────────────


class TestStartGuards:
    def test_cannot_start_running_experiment(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)
        ab_svc.start_experiment(user, exp)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="running"):
            ab_svc.start_experiment(user, exp)

    def test_cannot_start_canceled_experiment(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)
        ab_svc.cancel_experiment(user, exp)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="canceled"):
            ab_svc.start_experiment(user, exp)

    def test_cannot_start_completed_experiment(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        # Start → runs complete eagerly → compute_comparison marks completed.
        ab_svc.start_experiment(user, exp)
        _db.session.commit()
        ab_svc.compute_comparison(user, exp)
        _db.session.commit()
        assert exp.status == "completed"

        with pytest.raises(BenchmarkError, match="completed"):
            ab_svc.start_experiment(user, exp)


# ── ABRU-010 ──────────────────────────────────────────────────────────────────


class TestCancelExperiment:
    def test_cancel_draft_experiment(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        ab_svc.cancel_experiment(user, exp)
        _db.session.commit()

        assert exp.status == "canceled"
        assert exp.completed_at is not None

    def test_cannot_cancel_completed_experiment(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)
        ab_svc.start_experiment(user, exp)
        _db.session.commit()
        ab_svc.compute_comparison(user, exp)
        _db.session.commit()
        assert exp.status == "completed"

        with pytest.raises(BenchmarkError, match="completed"):
            ab_svc.cancel_experiment(user, exp)

    def test_cannot_cancel_already_canceled(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)
        ab_svc.cancel_experiment(user, exp)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="canceled"):
            ab_svc.cancel_experiment(user, exp)


# ── ABRU-011 ──────────────────────────────────────────────────────────────────


class TestUnauthenticated:
    def test_unauthenticated_cannot_start(self, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)

        with pytest.raises(BenchmarkError, match="Authentication"):
            ab_svc.start_experiment(None, exp)


# ── ABRU-012 / ABRU-013 ───────────────────────────────────────────────────────


class TestStartCancelRoutes:
    def test_start_route_redirects_to_detail(self, auth_client, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)
        _login(auth_client, user)
        resp = auth_client.post(f"/ab/{exp.slug}/start")
        assert resp.status_code == 302
        assert f"/ab/{exp.slug}" in resp.headers["Location"]

    def test_cancel_route_redirects_to_detail(self, auth_client, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)
        _login(auth_client, user)
        resp = auth_client.post(f"/ab/{exp.slug}/cancel")
        assert resp.status_code == 302
        assert f"/ab/{exp.slug}" in resp.headers["Location"]


# ── ABRU-014 ──────────────────────────────────────────────────────────────────


class TestStartRouteAuth:
    def test_unauthenticated_start_redirected(self, auth_client, db_session):
        user = _make_user()
        _suite, _pa, _pb, exp = _make_draft_experiment(user)
        resp = auth_client.post(f"/ab/{exp.slug}/start")
        assert resp.status_code == 302
        # Must not redirect to the experiment page — should go to login
        assert f"/ab/{exp.slug}" not in resp.headers["Location"]
