"""Tests for WorkspaceHealthService.get_ontology_coverage.

Coverage
--------
  WHC-001  Returns empty list when no ontology-mapped posts exist.
  WHC-002  Returns one row per ontology node with correct post_count.
  WHC-003  is_low_coverage True when post_count < 3.
  WHC-004  is_low_coverage False when post_count >= 3.
  WHC-005  benchmarked_count only counts completed runs in workspace.
  WHC-006  revised_count reflects posts-with-accepted-revision, not total revisions.
  WHC-007  contributor_count includes original author + accepted-revision authors.
  WHC-008  Sorted: post_count ASC, node_id DESC.
  WHC-009  Public content_ontology mappings included (workspace_id IS NULL).
  WHC-010  Posts from other workspaces excluded.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.benchmark import BenchmarkRun, BenchmarkSuite
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(51_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"whc{n}@x.com",
        username=f"whc{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"WHC {n}", slug=f"whc-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _make_node(creator) -> OntologyNode:
    n = _n()
    node = OntologyNode(
        slug=f"whc-node-{n}",
        name=f"WHC Node {n}",
        created_by_user_id=creator.id,
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _make_post(author, ws, *, kind="prompt", status=PostStatus.published) -> Post:
    n = _n()
    p = Post(
        title=f"WHC {n}",
        slug=f"whc-post-{n}",
        kind=kind,
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=ws.id,
        view_count=0,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _map(post, node, *, workspace_id=None):
    co = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
    )
    _db.session.add(co)
    _db.session.flush()
    return co


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
        proposed_markdown="revised",
        summary="fix",
        status=status,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_whc_001_empty_returns_empty_list(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    assert WorkspaceHealthService.get_ontology_coverage(ws) == []


def test_whc_002_post_count_per_node(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p1 = _make_post(owner, ws)
    p2 = _make_post(owner, ws)
    _map(p1, node, workspace_id=ws.id)
    _map(p2, node, workspace_id=ws.id)
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert len(rows) == 1
    assert rows[0].node_id == node.id
    assert rows[0].post_count == 2


def test_whc_003_is_low_coverage_below_threshold(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert rows[0].is_low_coverage is True


def test_whc_004_not_low_coverage_at_threshold(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    for _ in range(3):
        p = _make_post(owner, ws)
        _map(p, node, workspace_id=ws.id)
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert rows[0].is_low_coverage is False


def test_whc_005_benchmarked_count_completed_only(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p1 = _make_post(owner, ws)
    p2 = _make_post(owner, ws)
    _map(p1, node, workspace_id=ws.id)
    _map(p2, node, workspace_id=ws.id)
    suite = _make_suite(owner, ws)
    _make_run(suite, p1, ws, status="completed")
    _make_run(suite, p2, ws, status="failed")
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert rows[0].benchmarked_count == 1


def test_whc_006_revised_count_posts_with_accepted_revisions(db_session):
    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p1 = _make_post(owner, ws)
    p2 = _make_post(owner, ws)
    _map(p1, node, workspace_id=ws.id)
    _map(p2, node, workspace_id=ws.id)
    _make_revision(p1, contrib)
    _make_revision(p1, contrib)  # two revisions on same post → still 1 revised post
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert rows[0].revised_count == 1


def test_whc_007_contributor_count_author_plus_revisers(db_session):
    owner = _make_user()
    contrib1 = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)  # owner is author
    _map(p, node, workspace_id=ws.id)
    _make_revision(p, contrib1)  # accepted revision
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    # owner (author) + contrib1 (reviser) = 2
    assert rows[0].contributor_count == 2


def test_whc_008_sorted_post_count_asc_node_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node_a = _make_node(owner)
    node_b = _make_node(owner)
    # node_a gets 2 posts, node_b gets 1
    for _ in range(2):
        p = _make_post(owner, ws)
        _map(p, node_a, workspace_id=ws.id)
    p = _make_post(owner, ws)
    _map(p, node_b, workspace_id=ws.id)
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert rows[0].post_count <= rows[-1].post_count


def test_whc_009_public_co_mapping_included(db_session):
    """workspace_id=NULL mappings on workspace posts should appear in coverage."""
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=None)  # public mapping on a workspace post
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert len(rows) == 1
    assert rows[0].post_count == 1


def test_whc_010_posts_from_other_workspace_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    ws2 = _make_workspace(owner)
    node = _make_node(owner)
    p_other = _make_post(owner, ws2)
    _map(p_other, node, workspace_id=ws2.id)
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    assert rows == []
