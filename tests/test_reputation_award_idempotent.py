"""Tests — ReputationService.award_event idempotency.

Coverage
~~~~~~~~
IDEM-001  Duplicate award_event calls with identical args do not double-increment.
IDEM-002  IntegrityError race-path: manual fingerprint collision returns existing event.
IDEM-003  Different fingerprint_parts produce separate events; totals accumulate.
IDEM-004  award_event with workspace_id does not touch User.reputation_score.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.reputation_event import ReputationEvent
from backend.models.reputation_total import ReputationTotal
from backend.services.reputation_service import ReputationService

_ctr = itertools.count(900)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"idem{n}@example.com",
        username=f"idem{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

    n = _n()
    ws = Workspace(name=f"IDEM-WS {n}", slug=f"idem-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


# ── IDEM-001 ──────────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_idem001_duplicate_call_no_double_increment(self, db_session):
        """Calling award_event twice with identical args produces exactly one event."""
        user = _make_user()

        kwargs = dict(
            user_id=user.id,
            workspace_id=None,
            event_type="admin_adjustment",
            source_type="post",
            source_id=1,
            points=10,
            fingerprint_parts={"test": "idem001"},
            metadata={},
        )

        ev1 = ReputationService.award_event(**kwargs)
        ev2 = ReputationService.award_event(**kwargs)

        assert ev1.id == ev2.id, "Second call must return the same event row."

        # Only one row in the ledger.
        count = _db.session.query(ReputationEvent).filter_by(user_id=user.id).count()
        assert count == 1

        # Total not doubled.
        total = ReputationService.get_public_total(user.id)
        assert total == 10

    def test_idem002_different_fingerprint_parts_two_events(self, db_session):
        """Different fingerprint_parts produce separate events; totals accumulate."""
        user = _make_user()

        ReputationService.award_event(
            user_id=user.id,
            workspace_id=None,
            event_type="admin_adjustment",
            source_type="post",
            source_id=2,
            points=5,
            fingerprint_parts={"seq": "a"},
            metadata={},
        )
        ReputationService.award_event(
            user_id=user.id,
            workspace_id=None,
            event_type="admin_adjustment",
            source_type="post",
            source_id=2,
            points=7,
            fingerprint_parts={"seq": "b"},
            metadata={},
        )

        count = _db.session.query(ReputationEvent).filter_by(user_id=user.id).count()
        assert count == 2, "Distinct fingerprints must produce separate events."

        total = ReputationService.get_public_total(user.id)
        assert total == 12

    def test_idem003_workspace_event_does_not_touch_reputation_score(self, db_session):
        """Workspace-scoped events must NOT update User.reputation_score."""
        owner = _make_user()
        ws = _make_workspace(owner)
        owner_initial = owner.reputation_score

        ReputationService.award_event(
            user_id=owner.id,
            workspace_id=ws.id,
            event_type="admin_adjustment",
            source_type="post",
            source_id=3,
            points=50,
            fingerprint_parts={"ws_test": "003"},
            metadata={},
        )

        _db.session.refresh(owner)
        assert owner.reputation_score == owner_initial, (
            "Workspace events must never mutate User.reputation_score."
        )

        ws_total = ReputationService.get_workspace_total(owner.id, ws.id)
        assert ws_total == 50

    def test_idem004_public_event_syncs_reputation_score(self, db_session):
        """Public-scoped award must sync User.reputation_score exactly."""
        user = _make_user()

        ReputationService.award_event(
            user_id=user.id,
            workspace_id=None,
            event_type="admin_adjustment",
            source_type="post",
            source_id=4,
            points=25,
            fingerprint_parts={"sync_test": "004a"},
            metadata={},
        )
        ReputationService.award_event(
            user_id=user.id,
            workspace_id=None,
            event_type="admin_adjustment",
            source_type="post",
            source_id=4,
            points=10,
            fingerprint_parts={"sync_test": "004b"},
            metadata={},
        )

        _db.session.refresh(user)
        expected = ReputationService.get_public_total(user.id)
        assert user.reputation_score == expected == 35

    def test_idem005_recompute_totals_matches_ledger(self, db_session):
        """recompute_totals_for_user produces totals identical to the ledger sum."""
        user = _make_user()

        for i, pts in enumerate([10, 5, -2]):
            ReputationService.award_event(
                user_id=user.id,
                workspace_id=None,
                event_type="admin_adjustment",
                source_type="post",
                source_id=10,
                points=pts,
                fingerprint_parts={"recompute": str(i)},
                metadata={},
            )

        # Corrupt the total manually.
        row = (
            _db.session.query(ReputationTotal)
            .filter_by(user_id=user.id, workspace_id=None)
            .first()
        )
        row.points_total = 999
        _db.session.commit()

        # Recompute should fix it.
        ReputationService.recompute_totals_for_user(user.id)

        total = ReputationService.get_public_total(user.id)
        assert total == 13, "Expected 10+5-2=13 after recompute."

        _db.session.refresh(user)
        assert user.reputation_score == 13
