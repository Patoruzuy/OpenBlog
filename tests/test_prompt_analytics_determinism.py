"""Tests for Prompt Evolution Analytics — determinism and ordering.

Coverage
--------
  PAD-001  build_version_metrics always returns entries ordered by version ASC.
  PAD-002  Calling build_version_metrics twice gives identical results.
  PAD-003  fork_comparison entries are sorted by composite_score DESC.
  PAD-004  fork_comparison tie-breaking falls back to post_id DESC.
  PAD-005  compute_trend_label is idempotent (pure function).
  PAD-006  build_version_metrics total SQL query count stays bounded (≤ 8).
"""

from __future__ import annotations

import itertools
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import event as sa_event

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
from backend.models.post_version import PostVersion
from backend.services import prompt_analytics_service as svc
from backend.services.prompt_analytics_service import VersionMetrics

_ctr = itertools.count(32_000)


def _n() -> int:
    return next(_ctr)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user() -> object:
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"pad{n}@example.com",
        username=f"paduser{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id: int | None = None) -> Post:
    n = _n()
    p = Post(
        title=f"PAD-Prompt {n}",
        slug=f"pad-prompt-{n}",
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


def _make_fork(author, origin: Post, *, workspace_id: int | None = None) -> Post:
    n = _n()
    f = Post(
        title=f"PAD-Fork {n}",
        slug=f"pad-fork-{n}",
        kind="prompt",
        markdown_body="forked",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
        view_count=0,
    )
    _db.session.add(f)
    _db.session.flush()
    link = ContentLink(
        from_post_id=f.id,
        to_post_id=origin.id,
        link_type="derived_from",
        workspace_id=workspace_id,
    )
    _db.session.add(link)
    _db.session.flush()
    return f


def _make_suite(author) -> BenchmarkSuite:
    n = _n()
    suite = BenchmarkSuite(
        name=f"PAD Suite {n}",
        slug=f"pad-suite-{n}",
        workspace_id=None,
        created_by_user_id=author.id,
    )
    _db.session.add(suite)
    _db.session.flush()
    return suite


def _make_case(suite: BenchmarkSuite) -> BenchmarkCase:
    n = _n()
    case = BenchmarkCase(suite_id=suite.id, name=f"PAD Case {n}", input_json={})
    _db.session.add(case)
    _db.session.flush()
    return case


def _make_run(
    author, prompt: Post, suite: BenchmarkSuite, *, version: int = 1
) -> BenchmarkRun:
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=version,
        workspace_id=None,
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


@contextmanager
def _count_queries() -> Generator[list[int], None, None]:
    """Context manager that counts SQL SELECT statements executed."""
    counter: list[int] = [0]

    def on_before_execute(
        conn, clauseelement, multiparams, params, execution_options, *args
    ):  # noqa: ARG001
        counter[0] += 1

    sa_event.listen(_db.engine, "before_execute", on_before_execute)
    try:
        yield counter
    finally:
        sa_event.remove(_db.engine, "before_execute", on_before_execute)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_pad_001_version_metrics_ordered_ascending(db_session):
    """PAD-001: versions are always returned in ascending order regardless of insert order."""
    author = _make_user()
    prompt = _make_prompt(author)

    # Insert out of order deliberately
    _make_pv(prompt, version=3, offset_seconds=20)
    _make_pv(prompt, version=1, offset_seconds=0)
    _make_pv(prompt, version=2, offset_seconds=10)

    result = svc.build_version_metrics(prompt, workspace=None)

    versions = [m.version for m in result]
    assert versions == sorted(versions)


def test_pad_002_build_version_metrics_idempotent(db_session):
    """PAD-002: calling build_version_metrics twice returns the same result."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)
    _make_pv(prompt, version=2, offset_seconds=10)

    suite = _make_suite(author)
    case = _make_case(suite)
    run = _make_run(author, prompt, suite, version=1)
    _make_result(run, case, 0.7)

    first = svc.build_version_metrics(prompt, workspace=None)
    second = svc.build_version_metrics(prompt, workspace=None)

    assert first == second


def test_pad_003_fork_comparison_sorted_by_composite_score_desc(db_session):
    """PAD-003: fork comparison entries are sorted composite_score DESC."""
    author = _make_user()
    prompt = _make_prompt(author)
    fork1 = _make_fork(author, prompt)
    fork2 = _make_fork(author, prompt)

    suite = _make_suite(author)
    case1 = _make_case(suite)
    case2 = _make_case(suite)
    case3 = _make_case(suite)

    # Give fork2 a higher benchmark score so it ranks first
    run_origin = _make_run(author, prompt, suite, version=1)
    run_fork1 = _make_run(author, fork1, suite, version=1)
    run_fork2 = _make_run(author, fork2, suite, version=1)
    _make_result(run_origin, case1, 0.5)
    _make_result(run_fork1, case2, 0.6)
    _make_result(run_fork2, case3, 0.9)

    fc = svc.build_fork_comparison(prompt, workspace=None)

    scores = [e.composite_score for e in fc.entries]
    assert scores == sorted(scores, reverse=True)


def test_pad_004_fork_comparison_tiebreak_by_post_id_desc(db_session):
    """PAD-004: when composite scores are equal, higher post_id comes first."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_fork(author, prompt)
    _make_fork(author, prompt)

    # No benchmark runs — all composite scores will be 0.0
    # Tie-break: post_id DESC → fork2 (higher ID) first among forks
    fc = svc.build_fork_comparison(prompt, workspace=None)

    # All entries have composite_score == 0.0
    assert all(e.composite_score == 0.0 for e in fc.entries)

    # Among equal-score entries, higher post_id appears first
    ids = [e.post_id for e in fc.entries]
    # Verify the ids are sorted descending (within same score bucket)
    assert ids == sorted(ids, reverse=True)


def test_pad_005_compute_trend_label_idempotent(db_session):
    """PAD-005: compute_trend_label returns the same value when called multiple times."""
    m1 = VersionMetrics(
        version=1,
        updated_at=None,
        benchmark_avg=0.6,
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
        delta_benchmark=0.2,
    )

    first = svc.compute_trend_label([m1, m2])
    second = svc.compute_trend_label([m1, m2])

    assert first == second == "improving"


def test_pad_006_build_version_metrics_query_count_bounded(db_session):
    """PAD-006: build_version_metrics issues at most 8 SQL queries regardless of data."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1, offset_seconds=0)
    _make_pv(prompt, version=2, offset_seconds=10)

    suite = _make_suite(author)
    case = _make_case(suite)
    run = _make_run(author, prompt, suite, version=1)
    _make_result(run, case, 0.7)

    with _count_queries() as counter:
        svc.build_version_metrics(prompt, workspace=None)

    assert counter[0] <= 8, f"Expected ≤ 8 queries, got {counter[0]}"
