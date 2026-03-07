"""Tests — Public leaderboard.

Coverage
--------
  PUB-001  User with public reputation total appears in results.
  PUB-002  Workspace-only total excluded from public leaderboard.
  PUB-003  Higher points_total ranks first.
  PUB-004  Tie-break: equal points → higher user_id ranks first.
  PUB-005  limit parameter respected.
  PUB-006  GET /leaderboard returns 200 + Cache-Control: public, max-age=120.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.reputation_total import ReputationTotal
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.leaderboard_service import LeaderboardService

_ctr = itertools.count(1000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"pub{n}@example.com",
        username=f"pubuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_public_total(user, points: int) -> ReputationTotal:
    rt = ReputationTotal(user_id=user.id, workspace_id=None, points_total=points)
    _db.session.add(rt)
    _db.session.flush()
    return rt


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"PUB-WS {n}", slug=f"pub-ws-{n}", owner_id=owner.id)
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


def _make_workspace_total(user, workspace_id: int, points: int) -> ReputationTotal:
    rt = ReputationTotal(
        user_id=user.id, workspace_id=workspace_id, points_total=points
    )
    _db.session.add(rt)
    _db.session.flush()
    return rt


# ── PUB-001 ───────────────────────────────────────────────────────────────────


class TestPublicLeaderboard:
    def test_pub001_user_with_public_total_appears(self, db_session):
        user = _make_user()
        _make_public_total(user, 42)
        _db.session.commit()

        rows = LeaderboardService.get_public_leaderboard()

        assert any(r.user_id == user.id for r in rows)

    def test_pub002_workspace_only_total_excluded(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        _make_workspace_total(user, ws.id, 999)
        _db.session.commit()

        rows = LeaderboardService.get_public_leaderboard()

        assert not any(r.user_id == user.id for r in rows)

    def test_pub003_higher_points_ranks_first(self, db_session):
        user_a = _make_user()
        user_b = _make_user()
        _make_public_total(user_a, 100)
        _make_public_total(user_b, 200)
        _db.session.commit()

        rows = LeaderboardService.get_public_leaderboard()

        ids_ordered = [r.user_id for r in rows]
        assert ids_ordered.index(user_b.id) < ids_ordered.index(user_a.id)

    def test_pub004_tie_break_higher_user_id_first(self, db_session):
        user_a = _make_user()
        user_b = _make_user()
        # user_b has higher id; both same points
        _make_public_total(user_a, 50)
        _make_public_total(user_b, 50)
        _db.session.commit()

        rows = LeaderboardService.get_public_leaderboard()

        ids_ordered = [r.user_id for r in rows]
        assert ids_ordered.index(user_b.id) < ids_ordered.index(user_a.id)

    def test_pub005_limit_respected(self, db_session):
        users = [_make_user() for _ in range(5)]
        for u in users:
            _make_public_total(u, 10)
        _db.session.commit()

        rows = LeaderboardService.get_public_leaderboard(limit=3)

        assert len(rows) <= 3

    def test_pub006_route_200_and_cache_header(self, auth_client, db_session):
        resp = auth_client.get("/leaderboard")

        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc
        assert "max-age=120" in cc
