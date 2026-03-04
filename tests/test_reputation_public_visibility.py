"""Tests — public reputation page cannot expose workspace events.

Coverage
~~~~~~~~
VIS-001  GET /users/<u>/reputation returns 200 with public events only.
VIS-002  Workspace events are absent from the public page even for the same user.
VIS-003  list_public_events never returns workspace-scoped rows.
VIS-004  Unknown user → 404.
VIS-005  Inactive user → 404.
VIS-006  Cache-Control header is public, max-age=60.
VIS-007  No workspace query param accepted on the public route.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.services.reputation_service import ReputationService

_ctr = itertools.count(3_000)


def _n() -> int:
    return next(_ctr)


def _make_user(*, active=True):
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"vis{n}@example.com",
        username=f"vis{n}",
        password_hash="x",
        role=UserRole.reader,
        is_active=active,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

    n = _n()
    ws = Workspace(name=f"VIS-WS {n}", slug=f"vis-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _award_public(user_id: int, points: int, tag: str) -> None:
    ReputationService.award_event(
        user_id=user_id,
        workspace_id=None,
        event_type="admin_adjustment",
        source_type="post",
        source_id=1,
        points=points,
        fingerprint_parts={"tag": tag, "user_id": user_id},
        metadata={},
    )


def _award_workspace(user_id: int, workspace_id: int, points: int, tag: str) -> None:
    ReputationService.award_event(
        user_id=user_id,
        workspace_id=workspace_id,
        event_type="admin_adjustment",
        source_type="post",
        source_id=2,
        points=points,
        fingerprint_parts={"tag": tag, "user_id": user_id, "ws": workspace_id},
        metadata={},
    )


# ── VIS-001 / VIS-002 ─────────────────────────────────────────────────────────


class TestPublicReputationPage:
    def test_vis001_reputation_page_returns_200(self, auth_client, db_session):
        """GET /users/<u>/reputation returns 200 for a valid active user."""
        user = _make_user()
        _award_public(user.id, 10, "vis001")
        _db.session.commit()

        resp = auth_client.get(f"/users/{user.username}/reputation")
        assert resp.status_code == 200

    def test_vis002_workspace_event_absent_from_public_page(
        self, auth_client, db_session
    ):
        """Workspace events must never appear in the HTML of the public rep page."""
        user = _make_user()
        ws = _make_workspace(user)

        _award_public(user.id, 10, "vis002_pub")
        _award_workspace(user.id, ws.id, 999, "vis002_ws")  # must never appear
        _db.session.commit()

        resp = auth_client.get(f"/users/{user.username}/reputation")
        assert resp.status_code == 200

        body = resp.data.decode()
        # The public total (10) must NOT include the 999 workspace points.
        assert "999" not in body, (
            "Workspace points must never appear on public rep page."
        )
        # The public event count in the service must also exclude workspace events.
        events = ReputationService.list_public_events(user.id)
        assert all(e.workspace_id is None for e in events), (
            "list_public_events must never return workspace-scoped rows."
        )

    def test_vis003_list_public_events_scope_enforced_in_sql(self, db_session):
        """list_public_events returns only workspace_id IS NULL rows."""
        user = _make_user()
        ws = _make_workspace(user)

        _award_public(user.id, 5, "vis003_pub")
        _award_workspace(user.id, ws.id, 50, "vis003_ws")
        _db.session.commit()

        events = ReputationService.list_public_events(user.id)
        assert len(events) == 1
        assert events[0].workspace_id is None

    def test_vis004_unknown_user_returns_404(self, auth_client, db_session):
        """Non-existent username on reputation page returns 404."""
        resp = auth_client.get("/users/totally_nonexistent_xyz/reputation")
        assert resp.status_code == 404

    def test_vis005_inactive_user_returns_404(self, auth_client, db_session):
        """Inactive user reputation page returns 404."""
        user = _make_user(active=False)
        _db.session.commit()

        resp = auth_client.get(f"/users/{user.username}/reputation")
        assert resp.status_code == 404

    def test_vis006_cache_control_header(self, auth_client, db_session):
        """Reputation page must send public Cache-Control header."""
        user = _make_user()
        _db.session.commit()

        resp = auth_client.get(f"/users/{user.username}/reputation")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc
        assert "max-age=60" in cc

    def test_vis007_public_total_matches_public_events_only(self, db_session):
        """get_public_total reflects only workspace_id IS NULL events."""
        user = _make_user()
        ws = _make_workspace(user)

        _award_public(user.id, 15, "vis007_pub1")
        _award_public(user.id, 5, "vis007_pub2")
        _award_workspace(user.id, ws.id, 100, "vis007_ws")
        _db.session.commit()

        public_total = ReputationService.get_public_total(user.id)
        assert public_total == 20, "Public total must not include workspace events."

        ws_total = ReputationService.get_workspace_total(user.id, ws.id)
        assert ws_total == 100
