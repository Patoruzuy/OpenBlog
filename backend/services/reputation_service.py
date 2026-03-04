"""Reputation service — auditable ledger with cached aggregate totals.

All reputation mutations go through :meth:`ReputationService.award_event`.
Never mutate ``User.reputation_score`` directly from outside this service.

Idempotency
-----------
Every event has a SHA-256 fingerprint computed from its identifying fields
(see :meth:`_compute_fingerprint`).  The ``reputation_events`` table has a
UNIQUE constraint on ``fingerprint``.  If a concurrent INSERT races and wins,
we catch the ``IntegrityError``, let ``begin_nested()`` roll back the
savepoint, and return the existing row — totals are never double-incremented.

Scope isolation
---------------
Public total  : ``workspace_id IS NULL``  — visible via profile & leaderboard.
Workspace total: ``workspace_id = ws.id``  — accessible to workspace members only;
                 NEVER surfaced on public routes.

``User.reputation_score`` is synced ONLY from the public total so that
existing templates and admin sorts continue to work without modification.

Transaction ownership
---------------------
``award_event`` always commits at the end of its unit of work.  When called
from within another service (e.g. revision_service.accept), any pending ORM
state the caller has flushed-but-not-committed will be committed together
with the reputation row — this is intentional and keeps both writes atomic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.reputation_event import ReputationEvent
from backend.models.reputation_total import ReputationTotal
from backend.models.user import User


class ReputationService:
    """Static-method service for the reputation ledger."""

    # ── Points constants ──────────────────────────────────────────────────────

    #: Base points for an accepted revision.
    POINTS_REVISION_ACCEPTED: int = 15
    #: Additional points when the post is public (workspace_id IS NULL).
    POINTS_PUBLIC_BONUS: int = 5
    #: Penalty applied when a revision is rejected.
    POINTS_REVISION_REJECTED: int = -2
    #: Points awarded to a post author when their post receives an upvote.
    POINTS_VOTE_RECEIVED: int = 1
    #: Points awarded to the winning variant author in an A/B experiment.
    POINTS_AB_WIN: int = 10

    # ── Core ─────────────────────────────────────────────────────────────────

    @staticmethod
    def award_event(
        *,
        user_id: int,
        workspace_id: int | None,
        event_type: str,
        source_type: str,
        source_id: int,
        points: int,
        fingerprint_parts: dict,
        metadata: dict,
    ) -> ReputationEvent:
        """Insert a reputation event and update the cached total atomically.

        Idempotent: if the computed fingerprint already exists the existing
        event is returned and NO totals are changed.

        Parameters
        ----------
        fingerprint_parts:
            Caller-supplied fields that together uniquely distinguish this
            event from all other events of the same type.  Merged with
            system fields before hashing.
        metadata:
            Arbitrary JSON-serialisable context stored alongside the event
            (e.g. post_id, voter_id). Not used for fingerprinting.
        """
        fingerprint = ReputationService._compute_fingerprint(
            fingerprint_parts=fingerprint_parts,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
        )

        # Fast path: fingerprint already committed — return immediately.
        existing = db.session.scalar(
            select(ReputationEvent).where(ReputationEvent.fingerprint == fingerprint)
        )
        if existing is not None:
            return existing

        event = ReputationEvent(
            user_id=user_id,
            workspace_id=workspace_id,
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            points=points,
            fingerprint=fingerprint,
            _metadata_json=json.dumps(metadata, sort_keys=True, default=str),
        )

        # Use a savepoint so a concurrent duplicate INSERT rolls back only
        # the savepoint, keeping the outer session healthy.
        try:
            with db.session.begin_nested():
                db.session.add(event)
                db.session.flush()
        except IntegrityError:
            # Savepoint was rolled back automatically by begin_nested().__exit__.
            # Outer session is intact — fetch and return the winning row.
            return db.session.scalar(
                select(ReputationEvent).where(
                    ReputationEvent.fingerprint == fingerprint
                )
            )

        # ── Update aggregate cache ────────────────────────────────────────────
        ReputationService._upsert_total(user_id, workspace_id, points)

        # ── Sync User.reputation_score (public scope only) ────────────────────
        if workspace_id is None:
            new_public_total = ReputationService.get_public_total(user_id)
            db.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(reputation_score=new_public_total)
            )

        db.session.commit()
        return event

    # ── Read helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def get_public_total(user_id: int) -> int:
        """Return the cached public (``workspace_id IS NULL``) reputation total."""
        row = db.session.scalar(
            select(ReputationTotal).where(
                ReputationTotal.user_id == user_id,
                ReputationTotal.workspace_id.is_(None),
            )
        )
        return row.points_total if row is not None else 0

    @staticmethod
    def get_workspace_total(user_id: int, workspace_id: int) -> int:
        """Return the cached workspace-scoped reputation total."""
        row = db.session.scalar(
            select(ReputationTotal).where(
                ReputationTotal.user_id == user_id,
                ReputationTotal.workspace_id == workspace_id,
            )
        )
        return row.points_total if row is not None else 0

    @staticmethod
    def list_public_events(user_id: int, limit: int = 50) -> list[ReputationEvent]:
        """Return recent public events for *user_id*.

        Scope filtering is enforced in SQL; callers cannot override it.
        Always returns ``workspace_id IS NULL`` rows only.
        """
        return list(
            db.session.scalars(
                select(ReputationEvent)
                .where(
                    ReputationEvent.user_id == user_id,
                    ReputationEvent.workspace_id.is_(None),
                )
                .order_by(ReputationEvent.created_at.desc())
                .limit(limit)
            ).all()
        )

    @staticmethod
    def list_workspace_events(
        user_id: int,
        workspace_id: int,
        limit: int = 50,
    ) -> list[ReputationEvent]:
        """Return workspace-scoped events for *user_id* in *workspace_id*.

        This method must NEVER be called from a public route.
        Workspace membership check is the caller's responsibility.
        """
        return list(
            db.session.scalars(
                select(ReputationEvent)
                .where(
                    ReputationEvent.user_id == user_id,
                    ReputationEvent.workspace_id == workspace_id,
                )
                .order_by(ReputationEvent.created_at.desc())
                .limit(limit)
            ).all()
        )

    # ── Admin / ops ───────────────────────────────────────────────────────────

    @staticmethod
    def recompute_totals_for_user(user_id: int) -> None:
        """Recompute ``reputation_totals`` from the raw ledger for one user.

        For each distinct workspace_id (including NULL = public) found in
        ``reputation_events``, sums all points and replaces the cached row.
        Also re-syncs ``User.reputation_score`` from the public total.

        Safe to call multiple times (idempotent by design); useful after
        manual DB surgery or a data repair script.
        """
        # Aggregate ledger grouped by scope.
        scope_rows = db.session.execute(
            select(
                ReputationEvent.workspace_id,
                func.sum(ReputationEvent.points).label("total"),
            )
            .where(ReputationEvent.user_id == user_id)
            .group_by(ReputationEvent.workspace_id)
        ).all()

        # Delete existing totals for this user.
        existing = list(
            db.session.scalars(
                select(ReputationTotal).where(ReputationTotal.user_id == user_id)
            ).all()
        )
        for row in existing:
            db.session.delete(row)
        db.session.flush()

        public_total = 0
        for ws_id, raw_total in scope_rows:
            total = int(raw_total or 0)
            db.session.add(
                ReputationTotal(
                    user_id=user_id,
                    workspace_id=ws_id,
                    points_total=total,
                    updated_at=datetime.now(UTC),
                )
            )
            if ws_id is None:
                public_total = total

        # Sync the denormalised cache field.
        db.session.execute(
            update(User).where(User.id == user_id).values(reputation_score=public_total)
        )
        db.session.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _compute_fingerprint(
        *,
        fingerprint_parts: dict,
        user_id: int,
        workspace_id: int | None,
        event_type: str,
        source_type: str,
        source_id: int,
    ) -> str:
        """Compute a stable SHA-256 hex fingerprint.

        The canonical dict merges caller-supplied *fingerprint_parts* with
        system-level fields (system fields always shadow caller keys of the
        same name).  Keys are sorted before JSON serialisation so the hash
        is independent of insertion order.
        """
        canonical: dict = {
            **fingerprint_parts,
            # System fields always win.
            "user_id": user_id,
            "workspace_id": workspace_id,
            "event_type": event_type,
            "source_type": source_type,
            "source_id": source_id,
        }
        encoded = json.dumps(canonical, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _upsert_total(
        user_id: int,
        workspace_id: int | None,
        points: int,
    ) -> None:
        """Increment (or create) the ReputationTotal row for (user, scope)."""
        total = db.session.scalar(
            select(ReputationTotal).where(
                ReputationTotal.user_id == user_id,
                (
                    ReputationTotal.workspace_id.is_(None)
                    if workspace_id is None
                    else ReputationTotal.workspace_id == workspace_id
                ),
            )
        )
        if total is None:
            total = ReputationTotal(
                user_id=user_id,
                workspace_id=workspace_id,
                points_total=points,
                updated_at=datetime.now(UTC),
            )
            db.session.add(total)
        else:
            total.points_total += points
            total.updated_at = datetime.now(UTC)
        db.session.flush()
