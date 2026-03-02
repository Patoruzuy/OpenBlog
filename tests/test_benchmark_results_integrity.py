"""Tests for Benchmark result data integrity.

Coverage
--------
  RI-001  UNIQUE(run_id, case_id) constraint prevents duplicate results.
  RI-002  score_numeric stored and retrieved as expected (Decimal-compatible).
  RI-003  get_benchmark_summary_for_prompt returns correct run_count per version.
  RI-004  avg_score is None when no scored results exist.
  RI-005  avg_score computed correctly when scores are present.
  RI-006  suite_names lists distinct suite names for that version.
  RI-007  Multiple versions produce separate summary rows.
  RI-008  get_run_with_results returns run + loaded results + case names.
  RI-009  Bounded query: list_runs_for_prompt returns at most 50 rows.
  RI-010  Summary only includes runs in the requested workspace scope.
  RI-011  get_benchmark_summary_for_prompt returns empty for no runs.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from backend.extensions import db as _db
from backend.models.benchmark import (
    BenchmarkRun,
    BenchmarkRunResult,
)
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import benchmark_service as bsvc

_ctr = itertools.count(2000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"ri{n}@example.com",
        username=f"riuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"RI-WS {n}", slug=f"ri-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id,
            user_id=owner.id,
            role=WorkspaceMemberRole.owner,
        )
    )
    _db.session.flush()
    return ws


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"RI-Prompt {n}",
        slug=f"ri-prompt-{n}",
        kind="prompt",
        markdown_body="Hello {{name}}",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_case(suite):
    from backend.models.benchmark import BenchmarkCase  # noqa: PLC0415

    n = _n()
    c = BenchmarkCase(
        suite_id=suite.id,
        name=f"RI Case {n}",
        input_json={"name": "World"},
    )
    _db.session.add(c)
    _db.session.flush()
    return c


def _make_run(user, suite, prompt, *, version=1, workspace_id=None):
    """Create a BenchmarkRun row directly (bypass task dispatch)."""
    import datetime  # noqa: PLC0415

    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=version,
        workspace_id=workspace_id,
        model_name="gpt-4o-mini",
        status="completed",
        created_by_user_id=user.id,
        started_at=datetime.datetime.now(datetime.UTC),
        completed_at=datetime.datetime.now(datetime.UTC),
    )
    _db.session.add(run)
    _db.session.flush()
    return run


def _make_result(run, case, *, score=None):
    r = BenchmarkRunResult(
        run_id=run.id,
        case_id=case.id,
        output_text="some output",
        score_numeric=score,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


# ── RI-001 ─────────────────────────────────────────────────────────────────────


class TestUniqueConstraint:
    def test_duplicate_run_case_pair_rejected(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"Uniq Suite {_n()}")
        case = _make_case(suite)
        prompt = _make_prompt(user)
        _db.session.commit()

        run = _make_run(user, suite, prompt)
        _make_result(run, case)
        _db.session.commit()

        # Attempt duplicate
        dup = BenchmarkRunResult(
            run_id=run.id,
            case_id=case.id,
            output_text="duplicate",
        )
        _db.session.add(dup)
        with pytest.raises(IntegrityError):
            _db.session.flush()
        _db.session.rollback()


# ── RI-002 ─────────────────────────────────────────────────────────────────────


class TestScoreNumericStorage:
    def test_score_stored_and_retrieved(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"Score Suite {_n()}")
        case = _make_case(suite)
        prompt = _make_prompt(user)
        _db.session.commit()

        run = _make_run(user, suite, prompt)
        result = _make_result(run, case, score=Decimal("0.875"))
        _db.session.commit()

        stored = _db.session.get(BenchmarkRunResult, result.id)
        assert stored.score_numeric is not None
        assert float(stored.score_numeric) == pytest.approx(0.875)

    def test_null_score_allowed(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"NullScore Suite {_n()}")
        case = _make_case(suite)
        prompt = _make_prompt(user)
        _db.session.commit()

        run = _make_run(user, suite, prompt)
        result = _make_result(run, case, score=None)
        _db.session.commit()

        stored = _db.session.get(BenchmarkRunResult, result.id)
        assert stored.score_numeric is None


# ── RI-003 / RI-004 / RI-005 / RI-006 ────────────────────────────────────────


class TestGetBenchmarkSummary:
    def test_run_count_correct(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"Summary Suite {_n()}")
        prompt = _make_prompt(user)
        _db.session.commit()

        # 3 runs for version 1
        for _ in range(3):
            _make_run(user, suite, prompt, version=1)
        _db.session.commit()

        summary = bsvc.get_benchmark_summary_for_prompt(prompt)
        assert len(summary) == 1
        row = summary[0]
        assert row["version"] == 1
        assert row["run_count"] == 3

    def test_avg_score_none_when_no_scores(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"NoScore Suite {_n()}")
        case = _make_case(suite)
        prompt = _make_prompt(user)
        _db.session.commit()

        run = _make_run(user, suite, prompt)
        _make_result(run, case, score=None)  # no numeric score
        _db.session.commit()

        summary = bsvc.get_benchmark_summary_for_prompt(prompt)
        assert len(summary) == 1
        assert summary[0]["avg_score"] is None

    def test_avg_score_computed_correctly(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"AvgScore Suite {_n()}")
        case1 = _make_case(suite)
        case2 = _make_case(suite)
        prompt = _make_prompt(user)
        _db.session.commit()

        run = _make_run(user, suite, prompt, version=1)
        _make_result(run, case1, score=Decimal("0.5"))
        _make_result(run, case2, score=Decimal("1.0"))
        _db.session.commit()

        summary = bsvc.get_benchmark_summary_for_prompt(prompt)
        assert len(summary) == 1
        avg = summary[0]["avg_score"]
        assert avg is not None
        assert float(avg) == pytest.approx(0.75)

    def test_suite_names_listed(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, "Named Suite XYZ")
        prompt = _make_prompt(user)
        _db.session.commit()

        _make_run(user, suite, prompt, version=1)
        _db.session.commit()

        summary = bsvc.get_benchmark_summary_for_prompt(prompt)
        assert len(summary) == 1
        assert "Named Suite XYZ" in summary[0]["suite_names"]


# ── RI-007 ─────────────────────────────────────────────────────────────────────


class TestMultipleVersions:
    def test_separate_rows_per_version(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"MultiVer Suite {_n()}")
        prompt = _make_prompt(user)
        _db.session.commit()

        _make_run(user, suite, prompt, version=1)
        _make_run(user, suite, prompt, version=2)
        _make_run(user, suite, prompt, version=2)
        _db.session.commit()

        summary = bsvc.get_benchmark_summary_for_prompt(prompt)
        versions = {row["version"]: row for row in summary}
        assert 1 in versions
        assert 2 in versions
        assert versions[1]["run_count"] == 1
        assert versions[2]["run_count"] == 2


# ── RI-008 ─────────────────────────────────────────────────────────────────────


class TestGetRunWithResults:
    def test_returns_run_and_results(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"GWR Suite {_n()}")
        case = _make_case(suite)
        prompt = _make_prompt(user)
        _db.session.commit()

        run = _make_run(user, suite, prompt)
        _make_result(run, case)
        _db.session.commit()

        loaded = bsvc.get_run_with_results(user, run.id)
        assert loaded is not None
        assert loaded.id == run.id
        assert len(loaded.results) == 1

    def test_returns_none_for_unknown_run(self, db_session):
        user = _make_user()
        assert bsvc.get_run_with_results(user, 999_999) is None


# ── RI-009 ─────────────────────────────────────────────────────────────────────


class TestBoundedQuery:
    def test_list_runs_bounded_to_50(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, f"Bounded Suite {_n()}")
        prompt = _make_prompt(user)
        _db.session.commit()

        for _ in range(60):
            _make_run(user, suite, prompt, version=1)
        _db.session.commit()

        runs = bsvc.list_runs_for_prompt(user, prompt)
        assert len(runs) <= 50


# ── RI-010 ─────────────────────────────────────────────────────────────────────


class TestSummaryWorkspaceScope:
    def test_summary_excludes_runs_from_other_workspace(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)

        suite = bsvc.create_suite(owner_a, f"Scope Summary Suite {_n()}")
        prompt = _make_prompt(owner_a)
        _db.session.commit()

        _make_run(owner_a, suite, prompt, version=1, workspace_id=ws_a.id)
        _make_run(owner_b, suite, prompt, version=1, workspace_id=ws_b.id)
        _db.session.commit()

        summary_a = bsvc.get_benchmark_summary_for_prompt(prompt, workspace_id=ws_a.id)
        summary_b = bsvc.get_benchmark_summary_for_prompt(prompt, workspace_id=ws_b.id)

        count_a = summary_a[0]["run_count"] if summary_a else 0
        count_b = summary_b[0]["run_count"] if summary_b else 0
        assert count_a == 1
        assert count_b == 1


# ── RI-011 ─────────────────────────────────────────────────────────────────────


class TestSummaryEmptyForNoRuns:
    def test_empty_summary_when_no_runs(self, db_session):
        user = _make_user()
        prompt = _make_prompt(user)
        _db.session.commit()
        summary = bsvc.get_benchmark_summary_for_prompt(prompt)
        assert summary == []
