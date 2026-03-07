"""Tests — Leaderboard determinism and bounded query count.

Coverage
--------
  DET-001  Same inputs → identical ranked results on repeated calls.
  DET-002  Tie on points → user_id DESC is the stable tie-break.
  DET-003  get_public_leaderboard executes ≤ 2 SQL queries.
  DET-004  get_workspace_leaderboard executes ≤ 3 SQL queries.
  DET-005  get_public_ontology_leaderboard executes ≤ 4 SQL queries.
"""

from __future__ import annotations

import itertools

from sqlalchemy import event

from backend.extensions import db as _db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.reputation_total import ReputationTotal
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.leaderboard_service import LeaderboardService

_ctr = itertools.count(6000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"det{n}@example.com",
        username=f"detuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"DET-WS {n}", slug=f"det-ws-{n}", owner_id=owner.id)
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


def _make_node(creator) -> OntologyNode:
    n = _n()
    node = OntologyNode(
        slug=f"det-node-{n}",
        name=f"DET Node {n}",
        is_public=True,
        created_by_user_id=creator.id,
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _make_post(author) -> Post:
    n = _n()
    p = Post(
        title=f"DET Post {n}",
        slug=f"det-post-{n}",
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _map_post(post, node, creator) -> ContentOntology:
    mapping = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        created_by_user_id=creator.id,
        workspace_id=None,
    )
    _db.session.add(mapping)
    _db.session.flush()
    return mapping


def _make_public_total(user, points: int) -> ReputationTotal:
    rt = ReputationTotal(user_id=user.id, workspace_id=None, points_total=points)
    _db.session.add(rt)
    _db.session.flush()
    return rt


def _make_ws_total(user, workspace_id: int, points: int) -> ReputationTotal:
    rt = ReputationTotal(
        user_id=user.id, workspace_id=workspace_id, points_total=points
    )
    _db.session.add(rt)
    _db.session.flush()
    return rt


def _count_queries(fn, *args, **kwargs):
    """Execute *fn* and return (result, query_count)."""
    queries: list[str] = []

    def listener(conn, cursor, statement, params, context, executemany):  # noqa: PLR0913
        queries.append(statement)

    engine = _db.engine
    event.listen(engine, "before_cursor_execute", listener)
    try:
        result = fn(*args, **kwargs)
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return result, len(queries)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestLeaderboardDeterminism:
    def test_det001_repeated_calls_identical_order(self, db_session):
        users = [_make_user() for _ in range(4)]
        for i, u in enumerate(users):
            _make_public_total(u, (i + 1) * 10)
        _db.session.commit()

        rows_1 = LeaderboardService.get_public_leaderboard()
        rows_2 = LeaderboardService.get_public_leaderboard()

        assert [r.user_id for r in rows_1] == [r.user_id for r in rows_2]

    def test_det002_tie_break_user_id_desc(self, db_session):
        user_a = _make_user()
        user_b = _make_user()
        # user_b has higher id; both same points
        _make_public_total(user_a, 77)
        _make_public_total(user_b, 77)
        _db.session.commit()

        rows = LeaderboardService.get_public_leaderboard()
        ids = [r.user_id for r in rows]

        # Higher user_id first
        assert ids.index(user_b.id) < ids.index(user_a.id)
        # Rank is monotonically increasing
        assert [r.rank for r in rows] == list(range(1, len(rows) + 1))

    def test_det003_public_leaderboard_query_count(self, db_session):
        user = _make_user()
        _make_public_total(user, 10)
        _db.session.commit()

        _, count = _count_queries(LeaderboardService.get_public_leaderboard)

        assert count <= 2

    def test_det004_workspace_leaderboard_query_count(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _make_ws_total(owner, ws.id, 20)
        _db.session.commit()

        _, count = _count_queries(LeaderboardService.get_workspace_leaderboard, ws)

        assert count <= 3

    def test_det005_public_ontology_leaderboard_query_count(self, db_session):
        creator = _make_user()
        author = _make_user()
        node = _make_node(creator)
        post = _make_post(author)
        _map_post(post, node, creator)
        _make_public_total(author, 10)
        _db.session.commit()

        _, count = _count_queries(
            LeaderboardService.get_public_ontology_leaderboard, node
        )

        assert count <= 4
