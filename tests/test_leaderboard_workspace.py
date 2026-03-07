"""Tests — Workspace leaderboard.

Coverage
--------
  WS-001  Non-member receives 404 on GET /w/<slug>/leaderboard.
  WS-002  Member receives 200.
  WS-003  Cache-Control: private, no-store header is present.
  WS-004  Only workspace totals shown (public-only user is absent).
  WS-005  Non-member with a workspace total row is excluded (inner join).
  WS-006  Unauthenticated request is redirected (not 200).
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.reputation_total import ReputationTotal
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.leaderboard_service import LeaderboardService

_ctr = itertools.count(2000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"ws{n}@example.com",
        username=f"wsuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"WS {n}", slug=f"ws-lb-{n}", owner_id=owner.id)
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


# ── WS-001 ────────────────────────────────────────────────────────────────────


class TestWorkspaceLeaderboard:
    def test_ws001_non_member_gets_404(self, auth_client, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()

        _login(auth_client, outsider)
        resp = auth_client.get(f"/w/{ws.slug}/leaderboard")

        assert resp.status_code == 404

    def test_ws002_member_gets_200(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _make_ws_total(owner, ws.id, 30)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/leaderboard")

        assert resp.status_code == 200

    def test_ws003_cache_control_private_no_store(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/leaderboard")

        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "no-store" in cc

    def test_ws004_only_workspace_total_in_results(self, db_session):
        owner = _make_user()
        public_only_user = _make_user()
        ws = _make_workspace(owner)

        _make_ws_total(owner, ws.id, 50)
        # This user has only a public total — not a workspace member
        _make_public_total(public_only_user, 999)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_leaderboard(ws)

        user_ids = [r.user_id for r in rows]
        assert owner.id in user_ids
        assert public_only_user.id not in user_ids

    def test_ws005_non_member_with_total_excluded(self, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)

        _make_ws_total(owner, ws.id, 50)
        # Give outsider a workspace total row (orphaned / stale)
        _make_ws_total(outsider, ws.id, 999)
        _db.session.commit()

        rows = LeaderboardService.get_workspace_leaderboard(ws)

        user_ids = [r.user_id for r in rows]
        assert outsider.id not in user_ids

    def test_ws006_unauthenticated_is_redirected(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()

        resp = auth_client.get(f"/w/{ws.slug}/leaderboard")

        # require_auth redirects to login page
        assert resp.status_code in (302, 401)
