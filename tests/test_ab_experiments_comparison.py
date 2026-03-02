"""Tests for A/B Experiment comparison (compute_comparison).

Coverage
--------
  ABCO-001  Not-started experiment returns empty ExperimentComparison.
  ABCO-002  Cases joined by case_id (not position).
  ABCO-003  avg_score_a/b computed correctly.
  ABCO-004  delta = avg_score_b - avg_score_a.
  ABCO-005  count_matched = cases present in both runs.
  ABCO-006  count_total = union of all case_ids.
  ABCO-007  count_scored_a/b independent of matched count.
  ABCO-008  Missing result in run_a → output_a = None, score_a = None.
  ABCO-009  Missing result in run_b → output_b = None, score_b = None.
  ABCO-010  avg_score is None when no scored results exist.
  ABCO-011  CaseComparison.score_delta is None when one score is None.
  ABCO-012  CaseComparison.score_delta = score_b - score_a when both present.
  ABCO-013  _sync_completion marks experiment completed when both runs terminal.
  ABCO-014  _sync_completion does not change status when only one run terminal.
  ABCO-015  run_a_status / run_b_status accessible via ExperimentComparison.
  ABCO-016  Bounded to _MAX_COMPARISON_CASES (manual limit test).
  ABCO-017  Unauthenticated call to compute_comparison raises BenchmarkError.
  ABCO-018  Non-member compute_comparison raises BenchmarkError (workspace exp).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.extensions import db as _db
from backend.models.ab_experiment import (
    ABExperimentRun,
    ABExperimentStatus,
)
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import ab_experiment_service as ab_svc
from backend.services.benchmark_service import BenchmarkError

_ctr = itertools.count(3_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"abco{n}@example.com",
        username=f"abcouser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"ABCO-Prompt {n}", slug=f"abco-prompt-{n}", kind="prompt",
        markdown_body="Q: {{q}}", status=status,
        author_id=author.id, workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"ABCO Suite {n}", slug=f"abco-suite-{n}",
        created_by_user_id=user.id, workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_case(suite, name="Case X"):
    c = BenchmarkCase(
        suite_id=suite.id, name=name,
        input_json={"q": "test"},
    )
    _db.session.add(c)
    _db.session.flush()
    return c


def _make_run(user, suite, prompt, version=1, status=BenchmarkRunStatus.completed):
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=version,
        status=status.value,
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(run)
    _db.session.flush()
    return run


def _make_result(run, case, output="output", score=None):
    r = BenchmarkRunResult(
        run_id=run.id,
        case_id=case.id,
        output_text=output,
        score_numeric=float(score) if score is not None else None,
        created_at=datetime.now(UTC),
    )
    _db.session.add(r)
    _db.session.flush()
    return r


def _make_experiment(user, suite, pa, pb, *, va=1, vb=2):
    exp = ab_svc.create_experiment(user, "Compare-Exp", suite, pa, va, pb, vb)
    _db.session.commit()
    return exp


def _attach_runs(experiment, run_a, run_b):
    """Manually attach ABExperimentRun without calling start_experiment."""
    exp_run = ABExperimentRun(
        experiment_id=experiment.id,
        run_a_id=run_a.id,
        run_b_id=run_b.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(exp_run)
    experiment.status = ABExperimentStatus.running.value
    experiment.started_at = datetime.now(UTC)
    _db.session.commit()
    return exp_run


# ── ABCO-001 ──────────────────────────────────────────────────────────────────


class TestNotStarted:
    def test_empty_comparison_when_not_started(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()
        exp = _make_experiment(user, suite, pa, pb)

        cmp = ab_svc.compute_comparison(user, exp)

        assert cmp.cases == []
        assert cmp.avg_score_a is None
        assert cmp.avg_score_b is None
        assert cmp.count_total == 0
        assert cmp.count_matched == 0


# ── ABCO-002 ──────────────────────────────────────────────────────────────────


class TestJoinByCaseId:
    def test_cases_joined_by_case_id_not_position(self, db_session):
        """Run A has case 1 and 2; run B has case 2 and 3.
        Joined result should have 3 entries covering cases 1, 2, 3.
        Case 1 should have output_b=None; case 3 should have output_a=None.
        Case 2 is matched.
        """
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "C1")
        c2 = _make_case(suite, "C2")
        c3 = _make_case(suite, "C3")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        _make_result(run_a, c1, output="A1", score=0.8)
        _make_result(run_a, c2, output="A2", score=0.6)
        _make_result(run_b, c2, output="B2", score=0.7)
        _make_result(run_b, c3, output="B3", score=0.9)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)

        by_case = {cc.case_id: cc for cc in cmp.cases}
        assert len(by_case) == 3

        assert by_case[c1.id].output_a == "A1"
        assert by_case[c1.id].output_b is None

        assert by_case[c2.id].output_a == "A2"
        assert by_case[c2.id].output_b == "B2"

        assert by_case[c3.id].output_a is None
        assert by_case[c3.id].output_b == "B3"

        assert cmp.count_matched == 1  # only c2


# ── ABCO-003 / ABCO-004 ───────────────────────────────────────────────────────


class TestAverageScores:
    def test_avg_scores_and_delta_computed_correctly(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "S1")
        c2 = _make_case(suite, "S2")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        # A scores: 0.4, 0.6 → avg = 0.5
        # B scores: 0.7, 0.9 → avg = 0.8
        _make_result(run_a, c1, output="a1", score=0.4)
        _make_result(run_a, c2, output="a2", score=0.6)
        _make_result(run_b, c1, output="b1", score=0.7)
        _make_result(run_b, c2, output="b2", score=0.9)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)

        assert cmp.avg_score_a == pytest.approx(Decimal("0.5"), abs=Decimal("0.001"))
        assert cmp.avg_score_b == pytest.approx(Decimal("0.8"), abs=Decimal("0.001"))
        # delta = avg_b - avg_a = 0.3
        assert cmp.delta == pytest.approx(Decimal("0.3"), abs=Decimal("0.001"))


# ── ABCO-005 / ABCO-006 / ABCO-007 ───────────────────────────────────────────


class TestCounts:
    def test_counts_are_correct(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "CT1")
        c2 = _make_case(suite, "CT2")
        c3 = _make_case(suite, "CT3")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        # run_a has c1(scored), c2(scored)
        # run_b has c2(scored), c3(no score)
        _make_result(run_a, c1, output="a1", score=0.5)
        _make_result(run_a, c2, output="a2", score=0.5)
        _make_result(run_b, c2, output="b2", score=0.5)
        _make_result(run_b, c3, output="b3", score=None)  # unscored
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)

        assert cmp.count_total == 3     # c1, c2, c3
        assert cmp.count_matched == 1   # only c2 in both
        assert cmp.count_scored_a == 2  # c1, c2 have scores in A
        assert cmp.count_scored_b == 1  # only c2 has score in B (c3 score=None)


# ── ABCO-008 / ABCO-009 ───────────────────────────────────────────────────────


class TestMissingResults:
    def test_missing_run_a_result_yields_none_output(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "MR1")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        # Only run_b has a result for c1; run_a has nothing
        _make_result(run_b, c1, output="b-only", score=0.5)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)
        row = cmp.cases[0]
        assert row.output_a is None
        assert row.score_a is None
        assert row.output_b == "b-only"

    def test_missing_run_b_result_yields_none_output(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "MR2")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        _make_result(run_a, c1, output="a-only", score=0.3)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)
        row = cmp.cases[0]
        assert row.output_a == "a-only"
        assert row.output_b is None
        assert row.score_b is None


# ── ABCO-010 ──────────────────────────────────────────────────────────────────


class TestNoScores:
    def test_avg_score_is_none_when_no_scored_results(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "NS1")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        _make_result(run_a, c1, output="a", score=None)
        _make_result(run_b, c1, output="b", score=None)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)
        assert cmp.avg_score_a is None
        assert cmp.avg_score_b is None
        assert cmp.delta is None


# ── ABCO-011 / ABCO-012 ───────────────────────────────────────────────────────


class TestScoreDelta:
    def test_score_delta_none_when_one_score_missing(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "SD1")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        _make_result(run_a, c1, output="a", score=0.5)
        _make_result(run_b, c1, output="b", score=None)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)
        assert cmp.cases[0].score_delta is None

    def test_score_delta_correct_when_both_present(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        c1 = _make_case(suite, "SD2")
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        _make_result(run_a, c1, output="a", score=0.4)
        _make_result(run_b, c1, output="b", score=0.7)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)
        assert cmp.cases[0].score_delta == pytest.approx(
            Decimal("0.3"), abs=Decimal("0.001")
        )


# ── ABCO-013 / ABCO-014 ───────────────────────────────────────────────────────


class TestSyncCompletion:
    def test_sync_marks_completed_when_both_runs_terminal(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()
        exp = _make_experiment(user, suite, pa, pb)

        run_a = _make_run(user, suite, pa, status=BenchmarkRunStatus.completed)
        run_b = _make_run(user, suite, pb, status=BenchmarkRunStatus.completed)
        _attach_runs(exp, run_a, run_b)

        # Directly call _sync_completion (internal) via compute_comparison.
        cmp = ab_svc.compute_comparison(user, exp)
        _db.session.commit()

        assert cmp.experiment.status == "completed"

    def test_sync_does_not_complete_when_one_run_still_running(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()
        exp = _make_experiment(user, suite, pa, pb)

        run_a = _make_run(user, suite, pa, status=BenchmarkRunStatus.completed)
        run_b = _make_run(user, suite, pb, status=BenchmarkRunStatus.running)
        _attach_runs(exp, run_a, run_b)

        ab_svc.compute_comparison(user, exp)
        _db.session.commit()

        assert exp.status == "running"  # not completed yet


# ── ABCO-015 ──────────────────────────────────────────────────────────────────


class TestRunStatusProperties:
    def test_run_status_properties_reflect_underlying_runs(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()
        exp = _make_experiment(user, suite, pa, pb)

        run_a = _make_run(user, suite, pa, status=BenchmarkRunStatus.completed)
        run_b = _make_run(user, suite, pb, status=BenchmarkRunStatus.running)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)
        assert cmp.run_a_status == "completed"
        assert cmp.run_b_status == "running"

    def test_run_status_none_when_not_started(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()
        exp = _make_experiment(user, suite, pa, pb)  # no runs attached

        cmp = ab_svc.compute_comparison(user, exp)
        assert cmp.run_a_status is None
        assert cmp.run_b_status is None


# ── ABCO-016 ──────────────────────────────────────────────────────────────────


class TestBoundedCases:
    def test_comparison_bounded_to_max_cases(self, db_session):
        """Insert 110 cases; comparison should return at most 100."""
        from backend.services import ab_experiment_service as _ab  # noqa: PLC0415

        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)

        # Create 110 cases and results.
        cases = [_make_case(suite, f"Bulk-{i}") for i in range(110)]
        run_a = _make_run(user, suite, pa)
        run_b = _make_run(user, suite, pb)
        for c in cases:
            _make_result(run_a, c, output=f"a-{c.id}", score=0.5)
            _make_result(run_b, c, output=f"b-{c.id}", score=0.6)
        _db.session.commit()

        exp = _make_experiment(user, suite, pa, pb)
        _attach_runs(exp, run_a, run_b)

        cmp = ab_svc.compute_comparison(user, exp)
        # Each run is bounded to _MAX_COMPARISON_CASES=100 in the query.
        assert len(cmp.cases) <= _ab._MAX_COMPARISON_CASES


# ── ABCO-017 / ABCO-018 ───────────────────────────────────────────────────────


class TestAccessControl:
    def test_unauthenticated_raises(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()
        exp = _make_experiment(user, suite, pa, pb)

        with pytest.raises(BenchmarkError, match="Authentication"):
            ab_svc.compute_comparison(None, exp)

    def test_non_member_cannot_access_workspace_comparison(self, db_session):

        owner = _make_user()
        outsider = _make_user()
        n = _n()
        ws = Workspace(name=f"WS {n}", slug=f"ws-{n}", owner_id=owner.id)
        _db.session.add(ws)
        _db.session.flush()
        _db.session.add(
            WorkspaceMember(workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner)
        )
        _db.session.flush()
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        exp = ab_svc.create_experiment(
            owner, "WS-Co-Exp", suite, pa, 1, pb, 2, workspace=ws
        )
        _db.session.commit()

        with pytest.raises(BenchmarkError):
            ab_svc.compute_comparison(outsider, exp)
