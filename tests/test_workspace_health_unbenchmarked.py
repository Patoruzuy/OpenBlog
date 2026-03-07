"""Tests for WorkspaceHealthService.get_unbenchmarked_prompts.

Coverage
--------
  WHU-001  Returns empty list when all prompts are benchmarked.
  WHU-002  Returns unbenchmarked prompts with correct fields.
  WHU-003  Prompt benchmarked in another workspace still appears.
  WHU-004  Draft prompts excluded.
  WHU-005  Non-prompt kinds excluded.
  WHU-006  ontology_node_names populated from content_ontology join.
  WHU-007  Sorted: updated_at ASC, post_id DESC.
  WHU-008  Limit parameter respected.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.benchmark import BenchmarkRun, BenchmarkSuite
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(52_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"whu{n}@x.com",
        username=f"whu{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    n = _n()
    ws = Workspace(name=f"WHU {n}", slug=f"whu-{n}", owner_id=owner.id)
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
    ts = datetime.now(UTC) - timedelta(days=days_old)
    p = Post(
        title=f"WHU Post {n}",
        slug=f"whu-post-{n}",
        kind=kind,
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=ws.id,
        view_count=0,
        updated_at=ts,
        created_at=ts,
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


def _make_node(creator) -> OntologyNode:
    n = _n()
    node = OntologyNode(
        slug=f"whu-n-{n}", name=f"WHU Node {n}", created_by_user_id=creator.id
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _map(post, node, ws=None):
    co = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=ws.id if ws else None,
        created_by_user_id=post.author_id,
    )
    _db.session.add(co)
    _db.session.flush()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_whu_001_all_benchmarked_returns_empty(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p = _make_post(owner, ws)
    suite = _make_suite(owner, ws)
    _make_run(suite, p, ws)
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert result == []


def test_whu_002_returns_unbenchmarked_with_fields(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p = _make_post(owner, ws)
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert len(result) == 1
    item = result[0]
    assert item.post_id == p.id
    assert item.slug == p.slug
    assert item.title == p.title
    assert item.version == p.version


def test_whu_003_benchmarked_in_other_workspace_still_shows(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    ws2 = _make_workspace(owner)
    p = _make_post(owner, ws)
    suite2 = _make_suite(owner, ws2)
    _make_run(suite2, p, ws2)  # run in ws2, not ws
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert len(result) == 1  # still unbenchmarked in ws


def test_whu_004_draft_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, status=PostStatus.draft)
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert result == []


def test_whu_005_non_prompt_kinds_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, kind="article")
    _make_post(owner, ws, kind="playbook")
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert result == []


def test_whu_006_ontology_node_names_populated(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, ws)
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert len(result) == 1
    assert node.name in result[0].ontology_node_names


def test_whu_007_sorted_updated_at_asc_post_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p_older = _make_post(owner, ws, days_old=10)
    p_newer = _make_post(owner, ws, days_old=2)
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert result[0].post_id == p_older.id
    assert result[1].post_id == p_newer.id


def test_whu_008_limit_respected(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    for _ in range(5):
        _make_post(owner, ws)
    result = WorkspaceHealthService.get_unbenchmarked_prompts(ws, limit=3)
    assert len(result) == 3
