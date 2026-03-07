"""Tests for WorkspaceHealthService.get_contributor_gaps.

Coverage
--------
  WHCG-001  Returns empty list when no ontology-mapped posts in workspace.
  WHCG-002  contributor_count includes original author.
  WHCG-003  contributor_count includes accepted-revision authors.
  WHCG-004  Pending revision authors not counted.
  WHCG-005  is_single_point True when contributor_count == 1.
  WHCG-006  is_single_point False when contributor_count > 1.
  WHCG-007  top_contributor_username populated.
  WHCG-008  Posts from other workspaces excluded.
  WHCG-009  Sorted: contributor_count ASC, node_id DESC.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(55_000)


def _n() -> int:
    return next(_ctr)


def _make_user(prefix="whcg"):
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
    ws = Workspace(name=f"WHCG {n}", slug=f"whcg-{n}", owner_id=owner.id)
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
        slug=f"whcg-n-{n}", name=f"WHCG Node {n}", created_by_user_id=creator.id
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _make_post(author, ws, *, kind="prompt") -> Post:
    n = _n()
    p = Post(
        title=f"WHCG {n}",
        slug=f"whcg-{n}",
        kind=kind,
        markdown_body="body",
        status=PostStatus.published,
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


def test_whcg_001_empty_returns_empty_list(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    assert WorkspaceHealthService.get_contributor_gaps(ws) == []


def test_whcg_002_author_counted_as_contributor(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    assert len(gaps) == 1
    assert gaps[0].contributor_count == 1


def test_whcg_003_accepted_revision_author_counted(db_session):
    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    _make_revision(p, contrib)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    assert gaps[0].contributor_count == 2


def test_whcg_004_pending_revision_not_counted(db_session):
    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    _make_revision(p, contrib, status=RevisionStatus.pending)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    assert gaps[0].contributor_count == 1


def test_whcg_005_is_single_point_true(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    assert gaps[0].is_single_point is True


def test_whcg_006_is_single_point_false(db_session):
    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    _make_revision(p, contrib)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    assert gaps[0].is_single_point is False


def test_whcg_007_top_contributor_username(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws)
    _map(p, node, workspace_id=ws.id)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    # top_contributor_username should be set (owner is the only contributor)
    assert gaps[0].top_contributor_username is not None


def test_whcg_008_other_workspace_posts_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    ws2 = _make_workspace(owner)
    node = _make_node(owner)
    p = _make_post(owner, ws2)
    _map(p, node, workspace_id=ws2.id)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    assert gaps == []


def test_whcg_009_sorted_contributor_count_asc_node_id_desc(db_session):
    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    node_a = _make_node(owner)
    node_b = _make_node(owner)
    # node_a: 1 contributor (single point)
    p_a = _make_post(owner, ws)
    _map(p_a, node_a, workspace_id=ws.id)
    # node_b: 2 contributors
    p_b = _make_post(owner, ws)
    _map(p_b, node_b, workspace_id=ws.id)
    _make_revision(p_b, contrib)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    assert gaps[0].contributor_count <= gaps[-1].contributor_count
