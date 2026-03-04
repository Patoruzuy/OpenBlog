"""Tests — User.reputation_score stays in sync with the public ledger total.

Coverage
~~~~~~~~
SYNC-001  Multiple public events → reputation_score equals sum of public points.
SYNC-002  Workspace event → reputation_score unchanged.
SYNC-003  Mix of public + workspace events → only public sum in reputation_score.
SYNC-004  Negative public event decrements reputation_score.
SYNC-005  recompute_totals_for_user corrects a corrupted reputation_score.
SYNC-006  recompute_totals_for_user with only workspace events leaves reputation_score at 0.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.reputation_total import ReputationTotal
from backend.services.reputation_service import ReputationService

_ctr = itertools.count(5_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"sync{n}@example.com",
        username=f"sync{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

    n = _n()
    ws = Workspace(name=f"SYNC-WS {n}", slug=f"sync-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _pub(user_id: int, points: int, tag: str) -> None:
    ReputationService.award_event(
        user_id=user_id,
        workspace_id=None,
        event_type="admin_adjustment",
        source_type="post",
        source_id=1,
        points=points,
        fingerprint_parts={"sync_tag": tag, "uid": user_id},
        metadata={},
    )


def _ws(user_id: int, workspace_id: int, points: int, tag: str) -> None:
    ReputationService.award_event(
        user_id=user_id,
        workspace_id=workspace_id,
        event_type="admin_adjustment",
        source_type="post",
        source_id=2,
        points=points,
        fingerprint_parts={"sync_tag": tag, "uid": user_id, "wid": workspace_id},
        metadata={},
    )


# ── SYNC-001 ──────────────────────────────────────────────────────────────────


class TestReputationScoreSync:
    def test_sync001_multiple_public_events_sum_to_reputation_score(self, db_session):
        """reputation_score == sum of all public-scope award_event points."""
        user = _make_user()

        _pub(user.id, 20, "s001a")
        _pub(user.id, 10, "s001b")
        _pub(user.id, 5, "s001c")

        _db.session.refresh(user)
        assert user.reputation_score == 35
        assert ReputationService.get_public_total(user.id) == 35

    def test_sync002_workspace_event_does_not_change_reputation_score(self, db_session):
        """A workspace-scoped event must not touch User.reputation_score."""
        user = _make_user()
        ws = _make_workspace(user)

        initial = user.reputation_score
        _ws(user.id, ws.id, 100, "s002")

        _db.session.refresh(user)
        assert user.reputation_score == initial

    def test_sync003_mixed_events_only_public_in_reputation_score(self, db_session):
        """When user has both public and workspace events, only public total syncs."""
        user = _make_user()
        ws = _make_workspace(user)

        _pub(user.id, 15, "s003_pub")
        _ws(user.id, ws.id, 200, "s003_ws")

        _db.session.refresh(user)
        assert user.reputation_score == 15, (
            "reputation_score must equal public total (15), not include workspace (200)."
        )

    def test_sync004_negative_public_event_decrements_reputation_score(
        self, db_session
    ):
        """Negative award event decrements reputation_score (no floor in ledger)."""
        user = _make_user()

        _pub(user.id, 10, "s004a")
        _pub(user.id, -3, "s004b")

        _db.session.refresh(user)
        assert user.reputation_score == 7

    def test_sync005_recompute_fixes_corrupted_reputation_score(self, db_session):
        """recompute_totals_for_user corrects a manually corrupted reputation_score."""
        user = _make_user()

        _pub(user.id, 50, "s005a")
        _pub(user.id, 25, "s005b")

        # Manually corrupt the total and reputation_score.
        from sqlalchemy import update

        from backend.models.user import User

        _db.session.execute(
            update(User).where(User.id == user.id).values(reputation_score=9999)
        )
        row = (
            _db.session.query(ReputationTotal)
            .filter_by(user_id=user.id, workspace_id=None)
            .first()
        )
        row.points_total = 9999
        _db.session.commit()

        # Recompute from ledger.
        ReputationService.recompute_totals_for_user(user.id)

        _db.session.refresh(user)
        assert user.reputation_score == 75
        assert ReputationService.get_public_total(user.id) == 75

    def test_sync006_recompute_ws_only_user_has_zero_reputation_score(self, db_session):
        """If all events are workspace-scoped, reputation_score stays 0."""
        user = _make_user()
        ws = _make_workspace(user)

        _ws(user.id, ws.id, 500, "s006")

        ReputationService.recompute_totals_for_user(user.id)

        _db.session.refresh(user)
        assert user.reputation_score == 0

    def test_sync007_get_public_total_returns_zero_for_new_user(self, db_session):
        """get_public_total returns 0 for a user with no reputation events."""
        user = _make_user()
        _db.session.commit()

        assert ReputationService.get_public_total(user.id) == 0

    def test_sync008_list_public_events_bounded_by_limit(self, db_session):
        """list_public_events respects the limit parameter."""
        user = _make_user()

        for i in range(10):
            _pub(user.id, 1, f"s008_{i}")

        events_5 = ReputationService.list_public_events(user.id, limit=5)
        assert len(events_5) == 5

        events_all = ReputationService.list_public_events(user.id, limit=50)
        assert len(events_all) == 10
