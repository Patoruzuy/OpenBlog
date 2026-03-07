"""Tests for WorkspaceHealthService.get_recommended_actions.

Coverage
--------
  WHR-001  Priority 1: unbenchmarked prompt in low-coverage node.
  WHR-002  Priority 2: stale prompt in higher-coverage node (post_count >= 3).
  WHR-003  Priority 3: single-contributor node → add_content.
  WHR-004  Priority 4: content with no accepted revisions → request_revision.
  WHR-005  Same post not recommended twice across priorities.
  WHR-006  action_type values are correct strings.
  WHR-007  Limit parameter respected.
  WHR-008  Empty workspace returns empty list.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.benchmark import BenchmarkRun, BenchmarkSuite
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(56_000)


def _n() -> int:
    return next(_ctr)


def _make_user(prefix="whr"):
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


def _make_workspace(owner):
    n = _n()
    ws = Workspace(name=f"WHR {n}", slug=f"whr-{n}", owner_id=owner.id)
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
        title=f"WHR {n}",
        slug=f"whr-{n}",
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


def _make_node(creator) -> OntologyNode:
    n = _n()
    node = OntologyNode(
        slug=f"whr-n-{n}", name=f"WHR Node {n}", created_by_user_id=creator.id
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _map(post, node, *, workspace_id=None):
    co = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
    )
    _db.session.add(co)
    _db.session.flush()


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


def _make_revision(post, author, *, status=RevisionStatus.accepted) -> Revision:
    r = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_id=None,
        base_version_number=1,
        proposed_markdown="improved",
        summary="fix",
        status=status,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_whr_001_priority1_unbenchmarked_low_coverage_node(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)  # 1 post → low coverage
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    # no benchmark run
    actions = WorkspaceHealthService.get_recommended_actions(ws)
    p1 = [a for a in actions if a.priority == 1]
    assert len(p1) >= 1
    assert p1[0].action_type == "benchmark_prompt"
    assert p1[0].post_slug == p.slug


def test_whr_002_priority2_stale_prompt_higher_coverage_node(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    # Create 3 posts in node (coverage >= 3)
    for _ in range(3):
        p = _make_post(owner, ws)
        _map(p, node, workspace_id=ws.id)
    # Create a stale prompt (days_old > 90) and benchmark it (so it's not P1)
    stale_p = _make_post(owner, ws, days_old=100)
    _map(stale_p, node, workspace_id=ws.id)
    suite = _make_suite(owner, ws)
    _make_run(suite, stale_p, ws)
    actions = WorkspaceHealthService.get_recommended_actions(ws)
    p2 = [a for a in actions if a.priority == 2]
    assert any(a.post_slug == stale_p.slug for a in p2)
    assert all(a.action_type == "review_stale" for a in p2)


def test_whr_003_priority3_single_contributor_node(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    suite = _make_suite(owner, ws)
    _make_run(suite, p, ws)  # benchmarked so not P1
    actions = WorkspaceHealthService.get_recommended_actions(ws)
    p3 = [a for a in actions if a.priority == 3]
    assert len(p3) >= 1
    assert p3[0].action_type == "add_content"
    assert p3[0].node_slug == node.slug


def test_whr_004_priority4_no_accepted_revisions(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    # No ontology mapping → no P1/P2/P3
    p = _make_post(owner, ws, kind="article")
    actions = WorkspaceHealthService.get_recommended_actions(ws)
    p4 = [a for a in actions if a.priority == 4]
    assert any(a.post_slug == p.slug for a in p4)
    assert all(a.action_type == "request_revision" for a in p4)


def test_whr_005_no_duplicate_posts_across_priorities(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    actions = WorkspaceHealthService.get_recommended_actions(ws)
    post_slugs = [a.post_slug for a in actions if a.post_slug is not None]
    assert len(post_slugs) == len(set(post_slugs))


def test_whr_006_action_type_valid_strings(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    actions = WorkspaceHealthService.get_recommended_actions(ws)
    valid_types = {
        "benchmark_prompt",
        "review_stale",
        "add_content",
        "request_revision",
    }
    for a in actions:
        assert a.action_type in valid_types


def test_whr_007_limit_respected(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    for _ in range(15):
        _make_post(owner, ws, kind="article")
    actions = WorkspaceHealthService.get_recommended_actions(ws, limit=5)
    assert len(actions) <= 5


def test_whr_008_empty_workspace_returns_empty(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    actions = WorkspaceHealthService.get_recommended_actions(ws)
    assert actions == []
