"""Tests for Prompt Evolution Analytics — scope isolation.

Coverage
--------
  PSI-001  Workspace benchmark runs invisible in public scope (build_version_metrics).
  PSI-002  Workspace A's AB experiments invisible in workspace B's scope.
  PSI-003  Cross-workspace run not counted in a different workspace's metrics.
  PSI-004  Fork belonging to workspace B excluded from workspace A's fork_comparison.
  PSI-005  Workspace fork excluded from public fork_comparison.
  PSI-006  AB experiment from another workspace does not influence ab_wins/losses.
  PSI-007  Public scope returns None benchmark_avg when only workspace runs exist.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

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
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import prompt_analytics_service as svc

_ctr = itertools.count(31_000)


def _n() -> int:
    return next(_ctr)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user() -> object:
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"psi{n}@example.com",
        username=f"psiuser{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"PSI WS {n}", slug=f"psi-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    member = WorkspaceMember(
        workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
    )
    _db.session.add(member)
    _db.session.flush()
    return ws


def _make_prompt(author, *, workspace_id: int | None = None) -> Post:
    n = _n()
    p = Post(
        title=f"PSI-Prompt {n}",
        slug=f"psi-prompt-{n}",
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


def _make_suite(author, *, workspace_id: int | None = None) -> BenchmarkSuite:
    n = _n()
    suite = BenchmarkSuite(
        name=f"PSI Suite {n}",
        slug=f"psi-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=author.id,
    )
    _db.session.add(suite)
    _db.session.flush()
    return suite


def _make_case(suite: BenchmarkSuite) -> BenchmarkCase:
    n = _n()
    case = BenchmarkCase(suite_id=suite.id, name=f"PSI Case {n}", input_json={})
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


def _make_pv(prompt: Post, version: int = 1) -> PostVersion:
    pv = PostVersion(
        post_id=prompt.id,
        version_number=version,
        markdown_body="snapshot",
        created_at=datetime.now(UTC),
    )
    _db.session.add(pv)
    _db.session.flush()
    return pv


def _make_fork(author, origin: Post, *, workspace_id: int | None = None) -> Post:
    n = _n()
    f = Post(
        title=f"PSI-Fork {n}",
        slug=f"psi-fork-{n}",
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


def _make_ab_experiment(
    author,
    prompt_a: Post,
    version_a: int,
    prompt_b: Post,
    version_b: int,
    suite: BenchmarkSuite,
    *,
    workspace_id: int | None = None,
) -> ABExperiment:
    n = _n()
    exp = ABExperiment(
        name=f"PSI AB {n}",
        slug=f"psi-ab-{n}",
        suite_id=suite.id,
        workspace_id=workspace_id,
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


def test_psi_001_workspace_runs_invisible_in_public_scope(db_session):
    """PSI-001: workspace-scoped run produces no benchmark_avg in public scope."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case = _make_case(suite)
    ws_run = _make_run(author, prompt, suite, version=1, workspace_id=ws.id)
    _make_result(ws_run, case, 0.99)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert len(result) == 1
    assert result[0].benchmark_avg is None


def test_psi_002_ws_a_experiments_invisible_in_ws_b_scope(db_session):
    """PSI-002: AB experiment scoped to workspace A does not appear in workspace B scope."""
    author = _make_user()
    ws_a = _make_workspace(author)
    ws_b = _make_workspace(author)
    prompt = _make_prompt(author)
    opponent = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case1 = _make_case(suite)
    case2 = _make_case(suite)

    run_a = _make_run(author, prompt, suite, version=1)
    run_b = _make_run(author, opponent, suite, version=1)
    _make_result(run_a, case1, 0.9)
    _make_result(run_b, case2, 0.7)

    exp = _make_ab_experiment(
        author,
        prompt_a=prompt,
        version_a=1,
        prompt_b=opponent,
        version_b=1,
        suite=suite,
        workspace_id=ws_a.id,
    )
    _make_exp_run(exp, run_a, run_b)

    # Querying from workspace B → ws_a's experiment must not be visible
    result = svc.build_version_metrics(prompt, workspace=ws_b)

    entry = result[0]
    assert entry.ab_wins == 0


def test_psi_003_cross_workspace_run_not_counted(db_session):
    """PSI-003: run scoped to workspace A is not included when querying workspace B."""
    author = _make_user()
    ws_a = _make_workspace(author)
    ws_b = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case = _make_case(suite)
    ws_a_run = _make_run(author, prompt, suite, version=1, workspace_id=ws_a.id)
    _make_result(ws_a_run, case, 0.95)

    # Querying workspace B — ws_a's run should be excluded
    result = svc.build_version_metrics(prompt, workspace=ws_b)

    assert len(result) == 1
    assert result[0].benchmark_avg is None


def test_psi_004_fork_from_ws_b_excluded_in_ws_a(db_session):
    """PSI-004: fork scoped to workspace B is excluded when building fork_comparison for workspace A."""
    author = _make_user()
    ws_a = _make_workspace(author)
    ws_b = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_fork(author, prompt, workspace_id=ws_b.id)

    fc = svc.build_fork_comparison(prompt, workspace=ws_a)

    assert fc.fork_count == 0


def test_psi_005_workspace_fork_excluded_from_public(db_session):
    """PSI-005: workspace fork is invisible in public fork_comparison."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_fork(author, prompt, workspace_id=ws.id)

    fc = svc.build_fork_comparison(prompt, workspace=None)

    assert fc.fork_count == 0
    assert len(fc.entries) == 1  # origin only


def test_psi_006_ab_experiment_from_other_workspace_excluded(db_session):
    """PSI-006: AB experiment from workspace B does not affect workspace A scope."""
    author = _make_user()
    ws_a = _make_workspace(author)
    ws_b = _make_workspace(author)
    prompt = _make_prompt(author)
    opponent = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case1 = _make_case(suite)
    case2 = _make_case(suite)

    run_a = _make_run(author, prompt, suite, version=1)
    run_b = _make_run(author, opponent, suite, version=1)
    _make_result(run_a, case1, 0.9)
    _make_result(run_b, case2, 0.6)

    # Scoped to ws_b
    exp = _make_ab_experiment(
        author,
        prompt_a=prompt,
        version_a=1,
        prompt_b=opponent,
        version_b=1,
        suite=suite,
        workspace_id=ws_b.id,
    )
    _make_exp_run(exp, run_a, run_b)

    result = svc.build_version_metrics(prompt, workspace=ws_a)

    entry = result[0]
    assert entry.ab_wins == 0
    assert entry.ab_losses == 0


def test_psi_007_public_scope_none_benchmark_avg_with_only_ws_run(db_session):
    """PSI-007: only workspace-scoped run exists → public metrics show benchmark_avg=None."""
    author = _make_user()
    ws = _make_workspace(author)
    prompt = _make_prompt(author)
    _make_pv(prompt, version=1)

    suite = _make_suite(author)
    case = _make_case(suite)
    run = _make_run(author, prompt, suite, version=1, workspace_id=ws.id)
    _make_result(run, case, 0.77)

    result = svc.build_version_metrics(prompt, workspace=None)

    assert len(result) == 1
    assert result[0].benchmark_avg is None
