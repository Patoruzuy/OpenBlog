"""Tests for Prompt Evolution Analytics — public scope (build_version_metrics / build_fork_comparison).

Coverage
--------
  PAP-001  build_version_metrics returns empty list for prompt with no versions and no runs.
  PAP-002  build_version_metrics returns one entry per PostVersion even with no runs.
  PAP-003  build_version_metrics benchmark_avg is computed from BenchmarkRunResults (public runs).
  PAP-004  build_version_metrics excludes workspace-scoped benchmark runs.
  PAP-005  build_fork_comparison returns origin-only entry with no forks.
  PAP-006  build_fork_comparison includes published public forks.
  PAP-007  build_fork_comparison excludes draft forks from public scope.
  PAP-008  GET /prompts/<slug>/analytics renders 200 with version_metrics and fork_comparison in context.
"""

from __future__ import annotations

import itertools

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
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import prompt_analytics_service as svc

_ctr = itertools.count(28_000)


def _n() -> int:
    return next(_ctr)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(prefix: str = "pap") -> object:
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"{prefix}{n}@example.com",
        username=f"{prefix}user{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(
    author,
    *,
    workspace_id: int | None = None,
    status: PostStatus = PostStatus.published,
) -> Post:
    n = _n()
    p = Post(
        title=f"PAP-Prompt {n}",
        slug=f"pap-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        view_count=0,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_fork(
    author,
    origin: Post,
    *,
    workspace_id: int | None = None,
    status: PostStatus = PostStatus.published,
) -> Post:
    n = _n()
    f = Post(
        title=f"PAP-Fork {n}",
        slug=f"pap-fork-{n}",
        kind="prompt",
        markdown_body="forked",
        status=status,
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


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"PAP WS {n}", slug=f"pap-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    member = WorkspaceMember(
        workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
    )
    _db.session.add(member)
    _db.session.flush()
    return ws


def _make_suite(author, *, workspace_id: int | None = None) -> BenchmarkSuite:
    n = _n()
    suite = BenchmarkSuite(
        name=f"PAP Suite {n}",
        slug=f"pap-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=author.id,
    )
    _db.session.add(suite)
    _db.session.flush()
    return suite


def _make_case(suite: BenchmarkSuite) -> BenchmarkCase:
    n = _n()
    case = BenchmarkCase(suite_id=suite.id, name=f"PAP Case {n}", input_json={})
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
    status: BenchmarkRunStatus = BenchmarkRunStatus.completed,
) -> BenchmarkRun:
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=version,
        workspace_id=workspace_id,
        status=status.value,
        created_by_user_id=author.id,
    )
    _db.session.add(run)
    _db.session.flush()
    return run


def _make_result(
    run: BenchmarkRun, case: BenchmarkCase, score: float
) -> BenchmarkRunResult:
    result = BenchmarkRunResult(
        run_id=run.id,
        case_id=case.id,
        output_text="output",
        score_numeric=score,
    )
    _db.session.add(result)
    _db.session.flush()
    return result


def _make_pv(prompt: Post, version: int = 1) -> PostVersion:
    from datetime import UTC, datetime  # noqa: PLC0415

    pv = PostVersion(
        post_id=prompt.id,
        version_number=version,
        markdown_body="snapshot",
        created_at=datetime.now(UTC),
    )
    _db.session.add(pv)
    _db.session.flush()
    return pv


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_pap_001_empty_prompt_returns_empty_metrics(db_session):
    """PAP-001: no PostVersions, no runs → empty list."""
    author = _make_user()
    prompt = _make_prompt(author)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert result == []


def test_pap_002_version_metrics_one_entry_per_postversion(db_session):
    """PAP-002: one PostVersion row → one VersionMetrics entry, benchmark_avg=None."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert len(result) == 1
    assert result[0].version == 1
    assert result[0].benchmark_avg is None
    assert result[0].execution_count == 0


def test_pap_003_benchmark_avg_computed_from_public_runs(db_session):
    """PAP-003: completed public run with scores → benchmark_avg is correct avg."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case = _make_case(suite)
    run = _make_run(author, prompt, suite, version=1)
    _make_result(run, case, 0.8)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert len(result) == 1
    assert result[0].benchmark_avg == 0.8
    assert result[0].execution_count == 1


def test_pap_004_workspace_runs_excluded_from_public_scope(db_session):
    """PAP-004: workspace-scoped run is invisible in public scope."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case = _make_case(suite)
    ws_run = _make_run(author, prompt, suite, version=1, workspace_id=ws.id)
    _make_result(ws_run, case, 0.9)

    result = svc.build_version_metrics(prompt, workspace=None)

    # Version entry from PostVersion must exist, but avg must be None (ws run excluded)
    assert len(result) == 1
    assert result[0].benchmark_avg is None


def test_pap_005_fork_comparison_no_forks(db_session):
    """PAP-005: origin with no forks → ForkComparison with fork_count=0, 1 entry (origin)."""
    author = _make_user()
    prompt = _make_prompt(author)

    fc = svc.build_fork_comparison(prompt, workspace=None)

    assert fc.fork_count == 0
    assert len(fc.entries) == 1
    assert fc.entries[0].is_origin is True
    assert fc.entries[0].post_id == prompt.id


def test_pap_006_fork_comparison_includes_published_public_fork(db_session):
    """PAP-006: published public fork → appears in ForkComparison entries."""
    author = _make_user()
    prompt = _make_prompt(author)
    fork = _make_fork(author, prompt)

    fc = svc.build_fork_comparison(prompt, workspace=None)

    assert fc.fork_count == 1
    assert len(fc.entries) == 2
    fork_ids = {e.post_id for e in fc.entries}
    assert fork.id in fork_ids
    assert prompt.id in fork_ids


def test_pap_007_draft_fork_excluded_from_public(db_session):
    """PAP-007: draft fork is NOT included in public fork comparison."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_fork(author, prompt, status=PostStatus.draft)

    fc = svc.build_fork_comparison(prompt, workspace=None)

    assert fc.fork_count == 0
    assert len(fc.entries) == 1  # only origin
    assert fc.entries[0].is_origin is True


def test_pap_008_analytics_route_renders_200_with_new_context(db_session, client):
    """PAP-008: GET /prompts/<slug>/analytics → 200 with version_metrics + fork_comparison."""
    author = _make_user()
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    resp = client.get(f"/prompts/{prompt.slug}/analytics")

    assert resp.status_code == 200
    body = resp.data.decode()
    # Template sections for the new panels should appear
    assert "Version Metrics" in body
    assert "Fork Comparison" in body
