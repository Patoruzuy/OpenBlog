"""Tests for Prompt Evolution Analytics — workspace scope.

Coverage
--------
  PAW-001  build_version_metrics includes workspace-scoped runs when workspace provided.
  PAW-002  build_version_metrics includes both public and workspace runs when workspace provided.
  PAW-003  build_fork_comparison includes workspace-scoped published forks.
  PAW-004  build_fork_comparison excludes forks from a different workspace.
  PAW-005  build_fork_comparison scope badge is 'workspace' for workspace-owned forks.
  PAW-006  GET /w/<ws_slug>/prompts/<slug>/analytics returns 200 with new panels.
  PAW-007  Workspace analytics response carries Cache-Control: private, no-store.
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

_ctr = itertools.count(29_000)


def _n() -> int:
    return next(_ctr)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(prefix: str = "paw") -> object:
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


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"PAW WS {n}", slug=f"paw-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    member = WorkspaceMember(
        workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
    )
    _db.session.add(member)
    _db.session.flush()
    return ws


def _make_prompt(
    author,
    *,
    workspace_id: int | None = None,
    status: PostStatus = PostStatus.published,
) -> Post:
    n = _n()
    p = Post(
        title=f"PAW-Prompt {n}",
        slug=f"paw-prompt-{n}",
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
        title=f"PAW-Fork {n}",
        slug=f"paw-fork-{n}",
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


def _make_suite(author, *, workspace_id: int | None = None) -> BenchmarkSuite:
    n = _n()
    suite = BenchmarkSuite(
        name=f"PAW Suite {n}",
        slug=f"paw-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=author.id,
    )
    _db.session.add(suite)
    _db.session.flush()
    return suite


def _make_case(suite: BenchmarkSuite) -> BenchmarkCase:
    n = _n()
    case = BenchmarkCase(suite_id=suite.id, name=f"PAW Case {n}", input_json={})
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
        run_id=run.id, case_id=case.id, output_text="out", score_numeric=score
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


def test_paw_001_workspace_run_included_when_workspace_provided(db_session):
    """PAW-001: workspace-scoped run is included when workspace object is passed."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author, workspace_id=ws.id)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case = _make_case(suite)
    ws_run = _make_run(author, prompt, suite, version=1, workspace_id=ws.id)
    _make_result(ws_run, case, 0.75)

    result = svc.build_version_metrics(prompt, workspace=ws)

    assert len(result) == 1
    assert result[0].benchmark_avg == 0.75
    assert result[0].execution_count == 1


def test_paw_002_workspace_scope_includes_public_and_ws_runs(db_session):
    """PAW-002: both public and workspace runs contribute to benchmark_avg."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case = _make_case(suite)
    # Public run (score=0.6) + workspace run (score=0.8) → avg=0.7
    pub_run = _make_run(author, prompt, suite, version=1, workspace_id=None)
    ws_run = _make_run(author, prompt, suite, version=1, workspace_id=ws.id)
    _make_result(pub_run, case, 0.6)

    case2 = _make_case(suite)
    _make_result(ws_run, case2, 0.8)

    result = svc.build_version_metrics(prompt, workspace=ws)

    assert len(result) == 1
    assert result[0].benchmark_avg is not None
    # Average of 0.6 and 0.8 across two runs (one result each)
    assert abs(result[0].benchmark_avg - 0.7) < 0.001


def test_paw_003_fork_comparison_includes_workspace_forks(db_session):
    """PAW-003: workspace forks appear in fork_comparison when workspace provided."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author)
    fork = _make_fork(author, prompt, workspace_id=ws.id)

    fc = svc.build_fork_comparison(prompt, workspace=ws)

    assert fc.fork_count == 1
    fork_ids = {e.post_id for e in fc.entries}
    assert fork.id in fork_ids


def test_paw_004_fork_comparison_excludes_other_workspace_forks(db_session):
    """PAW-004: fork from a different workspace is excluded even when workspace provided."""
    author = _make_user()
    ws_a = _make_workspace(author)
    ws_b = _make_workspace(author)
    prompt = _make_prompt(author)
    # Fork belongs to ws_b — should not appear when querying ws_a scope
    _make_fork(author, prompt, workspace_id=ws_b.id)

    fc = svc.build_fork_comparison(prompt, workspace=ws_a)

    # ws_b fork is excluded; only origin appears
    assert fc.fork_count == 0


def test_paw_005_workspace_fork_scope_badge(db_session):
    """PAW-005: workspace fork entry has scope='workspace'."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_fork(author, prompt, workspace_id=ws.id)

    fc = svc.build_fork_comparison(prompt, workspace=ws)

    fork_entries = [e for e in fc.entries if not e.is_origin]
    assert len(fork_entries) == 1
    assert fork_entries[0].scope == "workspace"


def test_paw_006_ws_analytics_route_renders_200(db_session, client):
    """PAW-006: GET /w/<ws_slug>/prompts/<slug>/analytics → 200."""
    from backend.models.user import UserRole  # noqa: PLC0415
    from backend.services.auth_service import AuthService  # noqa: PLC0415

    n = _n()
    owner = AuthService.register(
        f"paw006owner{n}@example.com", f"paw006owner{n}", "StrongPass123!!"
    )
    owner.role = UserRole.editor
    _db.session.commit()
    token = AuthService.issue_access_token(owner)

    ws = _make_workspace(owner)
    prompt = _make_prompt(owner, workspace_id=ws.id)
    _make_pv(prompt, version=1)

    resp = client.get(
        f"/w/{ws.slug}/prompts/{prompt.slug}/analytics",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Version Metrics" in body


def test_paw_007_ws_analytics_response_has_no_store_header(db_session, client):
    """PAW-007: workspace analytics response has Cache-Control: private, no-store."""
    from backend.models.user import UserRole  # noqa: PLC0415
    from backend.services.auth_service import AuthService  # noqa: PLC0415

    n = _n()
    owner = AuthService.register(
        f"paw007owner{n}@example.com", f"paw007owner{n}", "StrongPass123!!"
    )
    owner.role = UserRole.editor
    _db.session.commit()
    token = AuthService.issue_access_token(owner)

    ws = _make_workspace(owner)
    prompt = _make_prompt(owner, workspace_id=ws.id)

    resp = client.get(
        f"/w/{ws.slug}/prompts/{prompt.slug}/analytics",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    cc = resp.headers.get("Cache-Control", "")
    assert "no-store" in cc
    assert "private" in cc
