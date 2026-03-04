"""Tests — revision acceptance/rejection reputation hooks.

Coverage
~~~~~~~~
REV-001  Accepted public revision awards POINTS_REVISION_ACCEPTED + PUBLIC_BONUS.
REV-002  Accepted workspace revision awards POINTS_REVISION_ACCEPTED only.
REV-003  Rejected revision applies POINTS_REVISION_REJECTED penalty.
REV-004  User.reputation_score is updated after a public acceptance.
REV-005  Accepting the same revision twice (idempotency) does not double award.
REV-006  Rejection event is workspace-scoped for workspace posts.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.reputation_service import ReputationService
from backend.services.revision_service import RevisionService

_ctr = itertools.count(1_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role="contributor"):
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"revhook{n}@example.com",
        username=f"revhook{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

    n = _n()
    ws = Workspace(name=f"RH-WS {n}", slug=f"rh-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _make_post(author, *, workspace_id=None):
    n = _n()
    p = Post(
        title=f"RH-Post {n}",
        slug=f"rh-post-{n}",
        markdown_body="original content",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_revision(post, author):
    r = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_number=post.version,
        proposed_markdown="improved content",
        summary="Fix typo",
        status=RevisionStatus.pending,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


# ── REV-001 / REV-004 ─────────────────────────────────────────────────────────


class TestRevisionAcceptHook:
    def test_rev001_public_revision_awards_accepted_plus_bonus(self, db_session):
        """accept() on a public post emits accepted + public bonus points."""
        post_author = _make_user("editor")
        contributor = _make_user("contributor")
        reviewer = _make_user("editor")

        post = _make_post(post_author, workspace_id=None)
        rev = _make_revision(post, contributor)
        _db.session.commit()

        RevisionService.accept(rev.id, reviewer.id)

        events = ReputationService.list_public_events(contributor.id)
        assert len(events) == 1, "Exactly one reputation event for contributor."
        ev = events[0]
        assert ev.event_type == "revision_accepted"
        assert ev.source_type == "revision"
        assert ev.source_id == rev.id
        assert ev.workspace_id is None
        expected_pts = (
            ReputationService.POINTS_REVISION_ACCEPTED
            + ReputationService.POINTS_PUBLIC_BONUS
        )
        assert ev.points == expected_pts, f"Expected {expected_pts}, got {ev.points}."

    def test_rev002_workspace_revision_no_public_bonus(self, db_session):
        """accept() on a workspace post awards POINTS_REVISION_ACCEPTED only."""
        post_author = _make_user("editor")
        contributor = _make_user("contributor")
        reviewer = _make_user("editor")

        ws = _make_workspace(post_author)
        post = _make_post(post_author, workspace_id=ws.id)
        rev = _make_revision(post, contributor)
        _db.session.commit()

        RevisionService.accept(rev.id, reviewer.id)

        # Event is workspace-scoped.
        ws_events = ReputationService.list_workspace_events(contributor.id, ws.id)
        assert len(ws_events) == 1
        ev = ws_events[0]
        assert ev.workspace_id == ws.id
        assert ev.points == ReputationService.POINTS_REVISION_ACCEPTED

        # Public events list must be empty (no leakage to public scope).
        public_events = ReputationService.list_public_events(contributor.id)
        assert public_events == [], (
            "Workspace revision event must not appear in public list."
        )

    def test_rev003_rejection_awards_negative_points(self, db_session):
        """reject() emits POINTS_REVISION_REJECTED penalty."""
        post_author = _make_user("editor")
        contributor = _make_user("contributor")
        reviewer = _make_user("editor")

        post = _make_post(post_author, workspace_id=None)
        rev = _make_revision(post, contributor)
        _db.session.commit()

        RevisionService.reject(rev.id, reviewer.id, note="Not appropriate.")

        events = ReputationService.list_public_events(contributor.id)
        assert len(events) == 1
        assert events[0].event_type == "revision_rejected"
        assert events[0].points == ReputationService.POINTS_REVISION_REJECTED

    def test_rev004_reputation_score_synced_after_public_acceptance(self, db_session):
        """User.reputation_score is updated from the public total on acceptance."""
        post_author = _make_user("editor")
        contributor = _make_user("contributor")
        reviewer = _make_user("editor")

        initial_score = contributor.reputation_score

        post = _make_post(post_author, workspace_id=None)
        rev = _make_revision(post, contributor)
        _db.session.commit()

        RevisionService.accept(rev.id, reviewer.id)

        _db.session.refresh(contributor)
        expected = initial_score + (
            ReputationService.POINTS_REVISION_ACCEPTED
            + ReputationService.POINTS_PUBLIC_BONUS
        )
        assert contributor.reputation_score == expected

    def test_rev005_acceptance_is_idempotent_via_fingerprint(self, db_session):
        """Manually calling award_event twice for the same revision is idempotent."""
        contributor = _make_user("contributor")

        ev1 = ReputationService.award_event(
            user_id=contributor.id,
            workspace_id=None,
            event_type="revision_accepted",
            source_type="revision",
            source_id=99,
            points=20,
            fingerprint_parts={"revision_id": 99},
            metadata={},
        )
        ev2 = ReputationService.award_event(
            user_id=contributor.id,
            workspace_id=None,
            event_type="revision_accepted",
            source_type="revision",
            source_id=99,
            points=20,
            fingerprint_parts={"revision_id": 99},
            metadata={},
        )

        assert ev1.id == ev2.id
        total = ReputationService.get_public_total(contributor.id)
        assert total == 20, "Total must not be doubled despite two identical calls."

    def test_rev006_rejection_workspace_scoped(self, db_session):
        """Rejected workspace revision event has the correct workspace_id."""
        post_author = _make_user("editor")
        contributor = _make_user("contributor")
        reviewer = _make_user("editor")

        ws = _make_workspace(post_author)
        post = _make_post(post_author, workspace_id=ws.id)
        rev = _make_revision(post, contributor)
        _db.session.commit()

        RevisionService.reject(rev.id, reviewer.id)

        ws_events = ReputationService.list_workspace_events(contributor.id, ws.id)
        assert len(ws_events) == 1
        assert ws_events[0].event_type == "revision_rejected"
        assert ws_events[0].workspace_id == ws.id

        pub_events = ReputationService.list_public_events(contributor.id)
        assert pub_events == [], "Workspace rejection must not appear in public events."
