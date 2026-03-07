"""Tests — Public ontology leaderboard.

Coverage
--------
  ONT-PUB-001  Author of public post with public mapping appears.
  ONT-PUB-002  Workspace-only mapping excluded.
  ONT-PUB-003  Workspace-only post excluded even if mapping is public.
  ONT-PUB-004  Descendant node posts included (BFS traversal).
  ONT-PUB-005  Ranking driven by public reputation_totals.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.reputation_total import ReputationTotal
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.leaderboard_service import LeaderboardService

_ctr = itertools.count(3000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"ontpub{n}@example.com",
        username=f"ontpubuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_node(creator, *, parent_id=None, is_public=True) -> OntologyNode:
    n = _n()
    node = OntologyNode(
        slug=f"node-{n}",
        name=f"Node {n}",
        is_public=is_public,
        created_by_user_id=creator.id,
        parent_id=parent_id,
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"ONT-P-WS {n}", slug=f"ont-p-ws-{n}", owner_id=owner.id)
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


def _make_post(author, *, workspace_id=None, status=PostStatus.published) -> Post:
    n = _n()
    p = Post(
        title=f"ONT Post {n}",
        slug=f"ont-post-{n}",
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


def _make_public_total(user, points: int) -> ReputationTotal:
    rt = ReputationTotal(user_id=user.id, workspace_id=None, points_total=points)
    _db.session.add(rt)
    _db.session.flush()
    return rt


# ── ONT-PUB-001 to ONT-PUB-005 ───────────────────────────────────────────────


class TestPublicOntologyLeaderboard:
    def test_ont_pub001_public_post_public_mapping_appears(self, db_session):
        creator = _make_user()
        author = _make_user()
        node = _make_node(creator)
        post = _make_post(author)
        _map_post(post, node, creator)
        _make_public_total(author, 50)
        _db.session.commit()

        rows = LeaderboardService.get_public_ontology_leaderboard(node)

        assert any(r.user_id == author.id for r in rows)

    def test_ont_pub002_workspace_only_mapping_excluded(self, db_session):
        creator = _make_user()
        author = _make_user()
        node = _make_node(creator)
        post = _make_post(author)
        ws = _make_workspace(creator)
        # Workspace-only mapping; no public mapping
        _map_post(post, node, creator, workspace_id=ws.id)
        _make_public_total(author, 50)
        _db.session.commit()

        rows = LeaderboardService.get_public_ontology_leaderboard(node)

        assert not any(r.user_id == author.id for r in rows)

    def test_ont_pub003_workspace_post_excluded(self, db_session):
        creator = _make_user()
        author = _make_user()
        node = _make_node(creator)
        ws = _make_workspace(creator)
        # Post is workspace-scoped but mapping is public
        post = _make_post(author, workspace_id=ws.id)
        _map_post(post, node, creator)  # workspace_id=None → public mapping
        _make_public_total(author, 50)
        _db.session.commit()

        rows = LeaderboardService.get_public_ontology_leaderboard(node)

        assert not any(r.user_id == author.id for r in rows)

    def test_ont_pub004_descendant_node_included(self, db_session):
        creator = _make_user()
        author = _make_user()
        parent = _make_node(creator)
        child = _make_node(creator, parent_id=parent.id)
        # Post is mapped to child node, not parent
        post = _make_post(author)
        _map_post(post, child, creator)
        _make_public_total(author, 50)
        _db.session.commit()

        # Query leaderboard for parent — should include child's contributors
        rows = LeaderboardService.get_public_ontology_leaderboard(parent)

        assert any(r.user_id == author.id for r in rows)

    def test_ont_pub005_ranking_by_public_reputation(self, db_session):
        creator = _make_user()
        author_a = _make_user()
        author_b = _make_user()
        node = _make_node(creator)

        post_a = _make_post(author_a)
        post_b = _make_post(author_b)
        _map_post(post_a, node, creator)
        _map_post(post_b, node, creator)

        _make_public_total(author_a, 10)
        _make_public_total(author_b, 100)
        _db.session.commit()

        rows = LeaderboardService.get_public_ontology_leaderboard(node)

        ids = [r.user_id for r in rows]
        assert ids.index(author_b.id) < ids.index(author_a.id)
