"""Tests for Workspace Health Dashboard — determinism.

All service functions must return stable, reproducible results for identical
data states. This file verifies deterministic ordering across all six
list-returning functions.

Coverage
--------
  WHD-001  get_ontology_coverage sorted by post_count ASC, node_id DESC.
  WHD-002  get_unbenchmarked_prompts sorted by updated_at ASC, post_id DESC.
  WHD-003  get_stale_content sorted by updated_at ASC, id DESC.
  WHD-004  get_unimproved_content sorted by created_at ASC, id DESC.
  WHD-005  get_contributor_gaps sorted by contributor_count ASC, node_id DESC.
  WHD-006  Calling each function twice returns identical results (stable reads).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(58_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"whd{n}@x.com",
        username=f"whd{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    n = _n()
    ws = Workspace(name=f"WHD {n}", slug=f"whd-{n}", owner_id=owner.id)
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
        slug=f"whd-n-{n}", name=f"WHD Node {n}", created_by_user_id=creator.id
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _make_post(
    author, ws, *, kind="prompt", status=PostStatus.published, days_old=0
) -> Post:
    n = _n()
    ts = datetime.now(UTC) - timedelta(days=days_old)
    p = Post(
        title=f"WHD {n}",
        slug=f"whd-{n}",
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


def _map(post, node, *, workspace_id=None):
    co = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
    )
    _db.session.add(co)
    _db.session.flush()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_whd_001_coverage_sorted_post_count_asc_node_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    node_a = _make_node(owner)
    node_b = _make_node(owner)
    node_c = _make_node(owner)
    # node_c gets 3 posts, node_b gets 2, node_a gets 1
    for i, (node, count) in enumerate([(node_c, 3), (node_b, 2), (node_a, 1)]):
        for _ in range(count):
            p = _make_post(owner, ws)
            _map(p, node, workspace_id=ws.id)
    rows = WorkspaceHealthService.get_ontology_coverage(ws)
    post_counts = [r.post_count for r in rows]
    # Must be non-decreasing
    assert post_counts == sorted(post_counts)
    # When post_counts equal, node_id must be descending
    for i in range(len(rows) - 1):
        if rows[i].post_count == rows[i + 1].post_count:
            assert rows[i].node_id > rows[i + 1].node_id


def test_whd_002_unbenchmarked_sorted_updated_at_asc_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p1 = _make_post(owner, ws, days_old=15)
    p2 = _make_post(owner, ws, days_old=10)
    p3 = _make_post(owner, ws, days_old=5)
    rows = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    assert rows[0].post_id == p1.id
    assert rows[1].post_id == p2.id
    assert rows[2].post_id == p3.id


def test_whd_003_stale_sorted_updated_at_asc_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p1 = _make_post(owner, ws, kind="article", days_old=300)
    p2 = _make_post(owner, ws, kind="article", days_old=200)
    p3 = _make_post(owner, ws, kind="article", days_old=100)
    rows = WorkspaceHealthService.get_stale_content(ws)
    assert rows[0].post_id == p1.id
    assert rows[1].post_id == p2.id
    assert rows[2].post_id == p3.id


def test_whd_004_unimproved_sorted_created_at_asc_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p1 = _make_post(owner, ws, kind="article", days_old=15)
    p2 = _make_post(owner, ws, kind="article", days_old=10)
    p3 = _make_post(owner, ws, kind="article", days_old=5)
    rows = WorkspaceHealthService.get_unimproved_content(ws)
    assert rows[0].post_id == p1.id
    assert rows[1].post_id == p2.id
    assert rows[2].post_id == p3.id


def test_whd_005_contributor_gaps_sorted_count_asc_node_id_desc(db_session):
    from backend.models.revision import Revision, RevisionStatus

    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    node_single = _make_node(owner)
    node_multi = _make_node(owner)
    # node_single: 1 contributor
    p_s = _make_post(owner, ws)
    _map(p_s, node_single, workspace_id=ws.id)
    # node_multi: 2 contributors
    p_m = _make_post(owner, ws)
    _map(p_m, node_multi, workspace_id=ws.id)
    r = Revision(
        post_id=p_m.id,
        author_id=contrib.id,
        base_version_id=None,
        base_version_number=1,
        proposed_markdown="x",
        summary="fix",
        status=RevisionStatus.accepted,
    )
    _db.session.add(r)
    _db.session.flush()
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    counts = [g.contributor_count for g in gaps]
    assert counts == sorted(counts)


def test_whd_006_double_call_same_results(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, kind="article", days_old=100)
    result_a = WorkspaceHealthService.get_stale_content(ws)
    result_b = WorkspaceHealthService.get_stale_content(ws)
    assert [r.post_id for r in result_a] == [r.post_id for r in result_b]
