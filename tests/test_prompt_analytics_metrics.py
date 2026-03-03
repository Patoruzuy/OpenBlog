"""Tests for Prompt Evolution Analytics — metrics math correctness.

Coverage
--------
  PAM-001  rating_delta is 0 for the very first version.
  PAM-002  rating_delta is correctly computed as votes gained since previous version.
  PAM-003  delta_benchmark is None for the first version (no prior baseline).
  PAM-004  delta_benchmark positive when benchmark improves between versions.
  PAM-005  delta_benchmark negative when benchmark regresses between versions.
  PAM-006  ab_wins attributed to the correct version of the prompt.
  PAM-007  ab_losses attributed to the correct version of the prompt.
  PAM-008  compute_trend_label returns 'improving' when latest bench > prev.
  PAM-009  compute_trend_label returns 'regressing' when latest bench < prev.
  PAM-010  compute_trend_label returns 'insufficient_data' when < 2 benchmarked versions.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.ab_experiment import (
    ABExperiment,
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
from backend.models.post_version import PostVersion
from backend.models.vote import Vote
from backend.services import prompt_analytics_service as svc
from backend.services.prompt_analytics_service import VersionMetrics

_ctr = itertools.count(30_000)


def _n() -> int:
    return next(_ctr)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user() -> object:
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"pam{n}@example.com",
        username=f"pamuser{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id: int | None = None) -> Post:
    n = _n()
    p = Post(
        title=f"PAM-Prompt {n}",
        slug=f"pam-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
        view_count=0,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_pv(prompt: Post, version: int, *, offset_seconds: int = 0) -> PostVersion:
    pv = PostVersion(
        post_id=prompt.id,
        version_number=version,
        markdown_body="snapshot",
        created_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds),
    )
    _db.session.add(pv)
    _db.session.flush()
    return pv


def _make_vote(author, prompt: Post, *, offset_seconds: int = 0) -> Vote:
    v = Vote(
        user_id=author.id,
        target_type="post",
        target_id=prompt.id,
        created_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds),
    )
    _db.session.add(v)
    _db.session.flush()
    return v


def _make_suite(author) -> BenchmarkSuite:
    n = _n()
    suite = BenchmarkSuite(
        name=f"PAM Suite {n}",
        slug=f"pam-suite-{n}",
        workspace_id=None,
        created_by_user_id=author.id,
    )
    _db.session.add(suite)
    _db.session.flush()
    return suite


def _make_case(suite: BenchmarkSuite) -> BenchmarkCase:
    n = _n()
    case = BenchmarkCase(suite_id=suite.id, name=f"PAM Case {n}", input_json={})
    _db.session.add(case)
    _db.session.flush()
    return case


def _make_run(
    author,
    prompt: Post,
    suite: BenchmarkSuite,
    *,
    version: int = 1,
    workspace_id: int | None = None,
) -> BenchmarkRun:
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=version,
        workspace_id=workspace_id,
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=author.id,
    )
    _db.session.add(run)
    _db.session.flush()
    return run


def _make_result(
    run: BenchmarkRun, case: BenchmarkCase, score: float
) -> BenchmarkRunResult:
    r = BenchmarkRunResult(
        run_id=run.id, case_id=case.id, output_text="out", score_numeric=score
    )
    _db.session.add(r)
    _db.session.flush()
    return r


def _make_ab_experiment(
    author, prompt_a: Post, version_a: int, prompt_b: Post, version_b: int, suite
) -> ABExperiment:
    n = _n()
    exp = ABExperiment(
        name=f"PAM AB Exp {n}",
        slug=f"pam-ab-exp-{n}",
        suite_id=suite.id,
        workspace_id=None,
        variant_a_prompt_post_id=prompt_a.id,
        variant_a_version=version_a,
        variant_b_prompt_post_id=prompt_b.id,
        variant_b_version=version_b,
        status=ABExperimentStatus.completed.value,
        created_by_user_id=author.id,
    )
    _db.session.add(exp)
    _db.session.flush()
    return exp


def _make_exp_run(
    exp: ABExperiment, run_a: BenchmarkRun, run_b: BenchmarkRun
) -> ABExperimentRun:
    er = ABExperimentRun(experiment_id=exp.id, run_a_id=run_a.id, run_b_id=run_b.id)
    _db.session.add(er)
    _db.session.flush()
    return er


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_pam_001_rating_delta_zero_for_first_version(db_session):
    """PAM-001: first version has rating_delta=0 regardless of votes after it."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)
    # Vote created after the v1 snapshot
    other = _make_user()
    _make_vote(other, prompt, offset_seconds=10)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert len(result) == 1
    assert result[0].rating_delta == 0  # no votes before the v1 snapshot


def test_pam_002_rating_delta_computed_correctly(db_session):
    """PAM-002: votes between v1 and v2 snapshots show up as rating_delta for v2."""
    author = _make_user()
    v1_author = _make_user()
    v2_author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)
    _make_pv(prompt, version=2, offset_seconds=100)

    # v1 snapshot is at T=0; v2 snapshot is at T=100
    # 2 votes at T=10 and T=20 (between v1 and v2)
    _make_vote(v1_author, prompt, offset_seconds=10)
    _make_vote(v2_author, prompt, offset_seconds=20)

    result = svc.build_version_metrics(prompt, workspace=None)

    v1_m = result[0]
    v2_m = result[1]

    # At v1 (T=0): no votes yet → rating_count=0, delta=0
    assert v1_m.rating_count == 0
    assert v1_m.rating_delta == 0

    # At v2 (T=100): 2 votes accumulated → rating_count=2, delta=2-0=2
    assert v2_m.rating_count == 2
    assert v2_m.rating_delta == 2


def test_pam_003_delta_benchmark_none_for_first_version(db_session):
    """PAM-003: delta_benchmark is None for the first benchmarked version."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)

    suite = _make_suite(author)
    case = _make_case(suite)
    run = _make_run(author, prompt, suite, version=1)
    _make_result(run, case, 0.6)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert result[0].delta_benchmark is None


def test_pam_004_delta_benchmark_positive_when_improving(db_session):
    """PAM-004: delta_benchmark is positive when benchmark avg increases."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)
    _make_pv(prompt, version=2, offset_seconds=10)

    suite = _make_suite(author)
    case1 = _make_case(suite)
    case2 = _make_case(suite)

    run_v1 = _make_run(author, prompt, suite, version=1)
    _make_result(run_v1, case1, 0.6)

    run_v2 = _make_run(author, prompt, suite, version=2)
    _make_result(run_v2, case2, 0.8)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert len(result) == 2
    assert result[0].delta_benchmark is None
    assert result[1].delta_benchmark is not None
    assert result[1].delta_benchmark > 0


def test_pam_005_delta_benchmark_negative_when_regressing(db_session):
    """PAM-005: delta_benchmark is negative when benchmark avg decreases."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)
    _make_pv(prompt, version=2, offset_seconds=10)

    suite = _make_suite(author)
    case1 = _make_case(suite)
    case2 = _make_case(suite)

    run_v1 = _make_run(author, prompt, suite, version=1)
    _make_result(run_v1, case1, 0.8)

    run_v2 = _make_run(author, prompt, suite, version=2)
    _make_result(run_v2, case2, 0.5)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert result[1].delta_benchmark is not None
    assert result[1].delta_benchmark < 0


def test_pam_006_ab_wins_attributed_to_correct_version(db_session):
    """PAM-006: ab_wins incremented on the version that won the experiment."""
    author = _make_user()
    prompt = _make_prompt(author)
    opponent = _make_prompt(author)
    _make_pv(prompt, version=2, offset_seconds=0)

    suite = _make_suite(author)
    case1 = _make_case(suite)
    case2 = _make_case(suite)

    # run_a is for the prompt we're testing (score 0.9) — higher = win
    run_a = _make_run(author, prompt, suite, version=2)
    run_b = _make_run(author, opponent, suite, version=1)
    _make_result(run_a, case1, 0.9)
    _make_result(run_b, case2, 0.7)

    exp = _make_ab_experiment(
        author,
        prompt_a=prompt,
        version_a=2,
        prompt_b=opponent,
        version_b=1,
        suite=suite,
    )
    _make_exp_run(exp, run_a, run_b)

    result = svc.build_version_metrics(prompt, workspace=None)

    entry_v2 = next(m for m in result if m.version == 2)
    assert entry_v2.ab_wins == 1
    assert entry_v2.ab_losses == 0


def test_pam_007_ab_losses_attributed_to_correct_version(db_session):
    """PAM-007: ab_losses incremented on the version that lost the experiment."""
    author = _make_user()
    prompt = _make_prompt(author)
    opponent = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)

    suite = _make_suite(author)
    case1 = _make_case(suite)
    case2 = _make_case(suite)

    # run_a for our prompt (score 0.4) — loses to opponent (0.9)
    run_a = _make_run(author, prompt, suite, version=1)
    run_b = _make_run(author, opponent, suite, version=1)
    _make_result(run_a, case1, 0.4)
    _make_result(run_b, case2, 0.9)

    exp = _make_ab_experiment(
        author,
        prompt_a=prompt,
        version_a=1,
        prompt_b=opponent,
        version_b=1,
        suite=suite,
    )
    _make_exp_run(exp, run_a, run_b)

    result = svc.build_version_metrics(prompt, workspace=None)

    entry_v1 = result[0]
    assert entry_v1.ab_wins == 0
    assert entry_v1.ab_losses == 1


def test_pam_008_compute_trend_label_improving(db_session):
    """PAM-008: trend label = 'improving' when latest benchmark > previous."""
    m1 = VersionMetrics(
        version=1,
        updated_at=None,
        benchmark_avg=0.5,
        execution_count=1,
        rating_count=0,
        rating_delta=0,
        ab_wins=0,
        ab_losses=0,
        delta_benchmark=None,
    )
    m2 = VersionMetrics(
        version=2,
        updated_at=None,
        benchmark_avg=0.8,
        execution_count=1,
        rating_count=0,
        rating_delta=0,
        ab_wins=0,
        ab_losses=0,
        delta_benchmark=0.3,
    )

    assert svc.compute_trend_label([m1, m2]) == "improving"


def test_pam_009_compute_trend_label_regressing(db_session):
    """PAM-009: trend label = 'regressing' when latest benchmark < previous."""
    m1 = VersionMetrics(
        version=1,
        updated_at=None,
        benchmark_avg=0.9,
        execution_count=1,
        rating_count=0,
        rating_delta=0,
        ab_wins=0,
        ab_losses=0,
        delta_benchmark=None,
    )
    m2 = VersionMetrics(
        version=2,
        updated_at=None,
        benchmark_avg=0.6,
        execution_count=1,
        rating_count=0,
        rating_delta=0,
        ab_wins=0,
        ab_losses=0,
        delta_benchmark=-0.3,
    )

    assert svc.compute_trend_label([m1, m2]) == "regressing"


def test_pam_010_compute_trend_label_insufficient_data(db_session):
    """PAM-010: trend label = 'insufficient_data' when fewer than 2 benchmarked versions."""
    m = VersionMetrics(
        version=1,
        updated_at=None,
        benchmark_avg=None,
        execution_count=0,
        rating_count=0,
        rating_delta=0,
        ab_wins=0,
        ab_losses=0,
        delta_benchmark=None,
    )

    assert svc.compute_trend_label([m]) == "insufficient_data"
    assert svc.compute_trend_label([]) == "insufficient_data"
