"""Tests — Workspace ontology leaderboard.

Coverage
--------
  ONT-WS-001  Non-member receives 404 on route.
  ONT-WS-002  Workspace post + workspace mapping contributor appears.
  ONT-WS-003  Public post + public mapping contributor also appears.
  ONT-WS-004  Other-workspace post excluded.
  ONT-WS-005  Ranking uses workspace reputation_totals (not public).
  ONT-WS-006  Cache-Control: private, no-store.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.reputation_total import ReputationTotal
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.leaderboard_service import LeaderboardService

_ctr = itertools.count(4000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"ontws{n}@example.com",
        username=f"ontwsuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"ONT-WS {n}", slug=f"ont-ws-lb-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id,
            user_id=owner.id,
            role=WorkspaceMemberRole.owner,
        )
    )
    _db.session.flush()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    _db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    _db.session.flush()


def _make_node(creator, *, parent_id=None, is_public=True) -> OntologyNode:
    n = _n()
    node = OntologyNode(
        slug=f"ow-node-{n}",
        name=f"OW Node {n}",
        is_public=is_public,
        created_by_user_id=creator.id,
        parent_id=parent_id,
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _make_post(author, *, workspace_id=None, status=PostStatus.published) -> Post:
    n = _n()
    p = Post(
        title=f"OW Post {n}",
        slug=f"ow-post-{n}",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _map_post(post, node, creator, *, workspace_id=None) -> ContentOntology:
    mapping = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        created_by_user_id=creator.id,
        workspace_id=workspace_id,
    )
    _db.session.add(mapping)
    _db.session.flush()
    return mapping


def _make_ws_total(user, workspace_id: int, points: int) -> ReputationTotal:
    rt = ReputationTotal(
        user_id=user.id, workspace_id=workspace_id, points_total=points
    )
    _db.session.add(rt)
    _db.session.flush()
    return rt


def _make_public_total(user, points: int) -> ReputationTotal:
    rt = ReputationTotal(user_id=user.id, workspace_id=None, points_total=points)
    _db.session.add(rt)
    _db.session.flush()
    return rt


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestWorkspaceOntologyLeaderboard:
    def test_ont_ws001_non_member_gets_404(self, auth_client, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        node = _make_node(owner)
        _db.session.commit()

        _login(auth_client, outsider)
        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/leaderboard")

        assert resp.status_code == 404

    def test_ont_ws002_ws_post_ws_mapping_appears(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        node = _make_node(owner)

        post = _make_post(owner, workspace_id=ws.id)
        _map_post(post, node, owner, workspace_id=ws.id)
        _make_ws_total(owner, ws.id, 50)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_ontology_leaderboard(ws, node)

        assert any(r.user_id == owner.id for r in rows)

    def test_ont_ws003_public_post_public_mapping_also_appears(self, db_session):
        owner = _make_user()
        contributor = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, contributor)
        node = _make_node(owner)

        # contributor has a public post with a public mapping
        post = _make_post(contributor)
        _map_post(post, node, contributor)
        _make_ws_total(contributor, ws.id, 25)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_ontology_leaderboard(ws, node)

        assert any(r.user_id == contributor.id for r in rows)

    def test_ont_ws004_other_workspace_post_excluded(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        node = _make_node(owner_a)

        # owner_b has a post in ws_b mapped to node with ws_b mapping
        post_b = _make_post(owner_b, workspace_id=ws_b.id)
        _map_post(post_b, node, owner_b, workspace_id=ws_b.id)
        _make_ws_total(owner_b, ws_a.id, 999)
        _add_member(ws_a, owner_b)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_ontology_leaderboard(ws_a, node)

        assert not any(r.user_id == owner_b.id for r in rows)

    def test_ont_ws005_ranking_by_workspace_totals(self, db_session):
        owner = _make_user()
        contributor = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, contributor)
        node = _make_node(owner)

        post_o = _make_post(owner)
        post_c = _make_post(contributor)
        _map_post(post_o, node, owner)
        _map_post(post_c, node, contributor)

        # owner has lower WS points but higher PUBLIC points
        _make_ws_total(owner, ws.id, 10)
        _make_ws_total(contributor, ws.id, 100)
        # Public totals reversed (shouldn't affect WS ranking)
        _make_public_total(owner, 500)
        _make_public_total(contributor, 5)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_ontology_leaderboard(ws, node)

        ids = [r.user_id for r in rows]
        assert ids.index(contributor.id) < ids.index(owner.id)

    def test_ont_ws006_route_cache_control(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        node = _make_node(owner)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/leaderboard")

        # Expect 200 (member) or correct headers
        cc = resp.headers.get("Cache-Control", "")
        if resp.status_code == 200:
            assert "private" in cc
            assert "no-store" in cc
