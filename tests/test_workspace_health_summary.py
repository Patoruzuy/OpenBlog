"""Tests for WorkspaceHealthService.get_health_summary.

Coverage
--------
  WHS-001  Returns zero counts for an empty workspace.
  WHS-002  total_prompts counts only published workspace prompts.
  WHS-003  benchmarked_prompts counts prompts with ≥1 completed run in workspace.
  WHS-004  Completed run in a different workspace does not count.
  WHS-005  stale_items counts published content older than 90 days.
  WHS-006  active_contributors counts distinct accepted-revision author_ids.
  WHS-007  Kind 'framework' is excluded from stale_items.
  WHS-008  Draft prompts are excluded from total_prompts.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.benchmark import BenchmarkRun, BenchmarkSuite
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(50_000)


def _n() -> int:
    return next(_ctr)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(prefix: str = "whs"):
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"{prefix}{n}@x.com",
        username=f"{prefix}{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"WHS {n}", slug=f"whs-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _make_post(
    author, ws, *, kind="prompt", status=PostStatus.published, days_old=0
) -> Post:
    n = _n()
    updated = datetime.now(UTC) - timedelta(days=days_old)
    p = Post(
        title=f"Post {n}",
        slug=f"post-{n}",
        kind=kind,
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=ws.id,
        view_count=0,
        updated_at=updated,
        created_at=updated,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_suite(author, ws) -> BenchmarkSuite:
    n = _n()
    s = BenchmarkSuite(
        name=f"S{n}", slug=f"s-{n}", workspace_id=ws.id, created_by_user_id=author.id
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_run(suite, prompt, ws, *, status="completed") -> BenchmarkRun:
    _n()
    r = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=1,
        workspace_id=ws.id,
        status=status,
        created_by_user_id=suite.created_by_user_id,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


def _make_revision(post, author, *, status=RevisionStatus.accepted) -> Revision:
    r = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_id=None,
        base_version_number=1,
        proposed_markdown="revised body",
        summary="fix",
        status=status,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_whs_001_empty_workspace(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    summary = WorkspaceHealthService.get_health_summary(ws)
    assert summary.total_prompts == 0
    assert summary.benchmarked_prompts == 0
    assert summary.stale_items == 0
    assert summary.active_contributors == 0


def test_whs_002_total_prompts_only_published_workspace(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, kind="prompt", status=PostStatus.published)
    _make_post(owner, ws, kind="prompt", status=PostStatus.draft)
    _make_post(owner, ws, kind="article", status=PostStatus.published)
    summary = WorkspaceHealthService.get_health_summary(ws)
    assert summary.total_prompts == 1


def test_whs_003_benchmarked_prompts_completed_run(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p1 = _make_post(owner, ws, kind="prompt")
    p2 = _make_post(owner, ws, kind="prompt")
    suite = _make_suite(owner, ws)
    _make_run(suite, p1, ws, status="completed")
    _make_run(suite, p2, ws, status="running")
    summary = WorkspaceHealthService.get_health_summary(ws)
    assert summary.total_prompts == 2
    assert summary.benchmarked_prompts == 1


def test_whs_004_completed_run_different_workspace_not_counted(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    ws2 = _make_workspace(owner)
    prompt = _make_post(owner, ws, kind="prompt")
    suite2 = _make_suite(owner, ws2)
    _make_run(suite2, prompt, ws2, status="completed")
    summary = WorkspaceHealthService.get_health_summary(ws)
    # ws has 1 prompt but 0 completed runs in ws
    assert summary.total_prompts == 1
    assert summary.benchmarked_prompts == 0


def test_whs_005_stale_items_over_90_days(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, kind="article", status=PostStatus.published, days_old=91)
    _make_post(owner, ws, kind="prompt", status=PostStatus.published, days_old=45)
    summary = WorkspaceHealthService.get_health_summary(ws)
    assert summary.stale_items == 1


def test_whs_006_active_contributors_distinct_revision_authors(db_session):
    owner = _make_user()
    contrib1 = _make_user()
    contrib2 = _make_user()
    ws = _make_workspace(owner)
    p = _make_post(owner, ws, kind="article")
    _make_revision(p, contrib1)
    _make_revision(p, contrib2)
    _make_revision(p, contrib2)  # duplicate — still counts once
    summary = WorkspaceHealthService.get_health_summary(ws)
    assert summary.active_contributors == 2


def test_whs_007_framework_excluded_from_stale(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, kind="framework", status=PostStatus.published, days_old=200)
    summary = WorkspaceHealthService.get_health_summary(ws)
    assert summary.stale_items == 0


def test_whs_008_draft_prompt_excluded_from_total(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, kind="prompt", status=PostStatus.draft)
    summary = WorkspaceHealthService.get_health_summary(ws)
    assert summary.total_prompts == 0
