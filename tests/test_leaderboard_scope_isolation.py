"""Tests — Leaderboard scope isolation.

Coverage
--------
  ISO-001  Workspace-A total never appears in Workspace-B leaderboard.
  ISO-002  Workspace-A post+mapping never appears in Workspace-B ontology leaderboard.
  ISO-003  Public leaderboard unaffected by workspace totals.
  ISO-004  Public ontology leaderboard contains zero workspace-only contributions.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.reputation_total import ReputationTotal
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.leaderboard_service import LeaderboardService

_ctr = itertools.count(5000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"iso{n}@example.com",
        username=f"isouser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"ISO-WS {n}", slug=f"iso-ws-{n}", owner_id=owner.id)
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


def _make_node(creator, *, is_public=True) -> OntologyNode:
    n = _n()
    node = OntologyNode(
        slug=f"iso-node-{n}",
        name=f"ISO Node {n}",
        is_public=is_public,
        created_by_user_id=creator.id,
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _make_post(author, *, workspace_id=None) -> Post:
    n = _n()
    p = Post(
        title=f"ISO Post {n}",
        slug=f"iso-post-{n}",
        markdown_body="body",
        status=PostStatus.published,
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


# ── ISO-001 to ISO-004 ────────────────────────────────────────────────────────


class TestLeaderboardScopeIsolation:
    def test_iso001_ws_a_total_absent_from_ws_b_leaderboard(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)

        # owner_a has a WS-A total but is not a member of WS-B
        _make_ws_total(owner_a, ws_a.id, 500)
        _make_ws_total(owner_b, ws_b.id, 10)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_leaderboard(ws_b)

        user_ids = [r.user_id for r in rows]
        assert owner_a.id not in user_ids
        assert owner_b.id in user_ids

    def test_iso002_ws_a_content_absent_from_ws_b_ontology_leaderboard(
        self, db_session
    ):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        creator = _make_user()
        node = _make_node(creator)

        # owner_a has a ws_a post mapped with ws_a mapping
        post_a = _make_post(owner_a, workspace_id=ws_a.id)
        _map_post(post_a, node, owner_a, workspace_id=ws_a.id)
        # owner_a also has a WS-B total but is not a member
        _make_ws_total(owner_a, ws_b.id, 999)
        _make_ws_total(owner_b, ws_b.id, 1)

        # owner_b is already a member (owner of ws_b)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_ontology_leaderboard(ws_b, node)

        user_ids = [r.user_id for r in rows]
        # owner_a appears in WS-B total but their content is from WS-A
        assert owner_a.id not in user_ids

    def test_iso003_public_leaderboard_unaffected_by_ws_totals(self, db_session):
        user = _make_user()
        owner = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, user)

        # User has a WS total but NO public total
        _make_ws_total(user, ws.id, 9999)
        _db.session.commit()

        rows = LeaderboardService.get_public_leaderboard()

        assert not any(r.user_id == user.id for r in rows)

    def test_iso004_public_ontology_zero_ws_contributions(self, db_session):
        creator = _make_user()
        ws_owner = _make_user()
        ws = _make_workspace(ws_owner)
        author = _make_user()
        node = _make_node(creator)

        # Post is workspace-scoped; mapping is workspace-scoped
        post = _make_post(author, workspace_id=ws.id)
        _map_post(post, node, author, workspace_id=ws.id)
        _make_public_total(author, 100)
        _db.session.commit()

        rows = LeaderboardService.get_public_ontology_leaderboard(node)

        assert not any(r.user_id == author.id for r in rows)
