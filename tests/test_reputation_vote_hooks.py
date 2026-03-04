"""Tests — vote service reputation hooks.

Coverage
~~~~~~~~
VOTE-001  Upvoting a post creates a vote_received event on the post author.
VOTE-002  User.reputation_score incremented after upvote of public post.
VOTE-003  Unvoting creates a negative vote_received event.
VOTE-004  User.reputation_score decremented after unvote.
VOTE-005  Duplicate upvote (same voter + post) raises VoteError, no extra event.
VOTE-006  Vote on workspace post creates workspace-scoped event.
VOTE-007  Comment votes do not generate reputation events.
VOTE-008  Self-voting raises VoteError (no reputation event created).
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services.reputation_service import ReputationService
from backend.services.vote_service import VoteError, VoteService

_ctr = itertools.count(2_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"vh{n}@example.com",
        username=f"vh{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

    n = _n()
    ws = Workspace(name=f"VH-WS {n}", slug=f"vh-ws-{n}", owner_id=owner.id)
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
        title=f"VH-Post {n}",
        slug=f"vh-post-{n}",
        markdown_body="content",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


# ── VOTE-001 / VOTE-002 ───────────────────────────────────────────────────────


class TestUpvoteHook:
    def test_vote001_upvote_creates_vote_received_event(self, db_session):
        """Upvoting a post generates a vote_received event for the post author."""
        voter = _make_user()
        author = _make_user()
        post = _make_post(author, workspace_id=None)
        _db.session.commit()

        VoteService.upvote(voter.id, "post", post.id)

        events = ReputationService.list_public_events(author.id)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "vote_received"
        assert ev.source_type == "post"
        assert ev.source_id == post.id
        assert ev.points == ReputationService.POINTS_VOTE_RECEIVED
        assert ev.workspace_id is None

    def test_vote002_upvote_syncs_reputation_score(self, db_session):
        """User.reputation_score is incremented after upvote of a public post."""
        voter = _make_user()
        author = _make_user()
        post = _make_post(author, workspace_id=None)
        initial = author.reputation_score
        _db.session.commit()

        VoteService.upvote(voter.id, "post", post.id)

        _db.session.refresh(author)
        assert (
            author.reputation_score == initial + ReputationService.POINTS_VOTE_RECEIVED
        )

    def test_vote003_unvote_creates_negative_event(self, db_session):
        """Unvoting creates a negative vote_received event cancelling the prior gain."""
        voter = _make_user()
        author = _make_user()
        post = _make_post(author, workspace_id=None)
        _db.session.commit()

        VoteService.upvote(voter.id, "post", post.id)
        VoteService.unvote(voter.id, "post", post.id)

        events = ReputationService.list_public_events(author.id)
        # Two events: +1 and -1
        assert len(events) == 2

        neg = next(e for e in events if e.points < 0)
        assert neg.points == -ReputationService.POINTS_VOTE_RECEIVED

    def test_vote004_unvote_decrements_reputation_score(self, db_session):
        """User.reputation_score is decremented after unvoting."""
        voter = _make_user()
        author = _make_user()
        post = _make_post(author, workspace_id=None)
        initial = author.reputation_score
        _db.session.commit()

        VoteService.upvote(voter.id, "post", post.id)
        VoteService.unvote(voter.id, "post", post.id)

        _db.session.refresh(author)
        assert author.reputation_score == initial

    def test_vote005_duplicate_upvote_raises_error_no_extra_event(self, db_session):
        """Duplicate vote raises VoteError; reputation event count unchanged."""
        voter = _make_user()
        author = _make_user()
        post = _make_post(author, workspace_id=None)
        _db.session.commit()

        VoteService.upvote(voter.id, "post", post.id)
        with pytest.raises(VoteError, match="Already voted"):
            VoteService.upvote(voter.id, "post", post.id)

        events = ReputationService.list_public_events(author.id)
        assert len(events) == 1, (
            "Duplicate vote must not create an extra reputation event."
        )

    def test_vote006_workspace_post_vote_scoped_correctly(self, db_session):
        """Vote on a workspace post creates workspace-scoped event."""
        voter = _make_user()
        author = _make_user()
        ws = _make_workspace(author)
        post = _make_post(author, workspace_id=ws.id)
        _db.session.commit()

        VoteService.upvote(voter.id, "post", post.id)

        # Workspace-scoped event exists.
        ws_events = ReputationService.list_workspace_events(author.id, ws.id)
        assert len(ws_events) == 1
        assert ws_events[0].workspace_id == ws.id

        # Must NOT appear in public events.
        pub_events = ReputationService.list_public_events(author.id)
        assert pub_events == [], "Workspace post vote must not be in public events."

        # User.reputation_score must NOT change for workspace votes.
        _db.session.refresh(author)
        assert author.reputation_score == 0

    def test_vote007_comment_vote_no_reputation_event(self, db_session):
        """Upvoting a comment does not create a reputation event (by design)."""
        from backend.models.comment import Comment

        voter = _make_user()
        author = _make_user()
        post = _make_post(author, workspace_id=None)

        comment = Comment(
            post_id=post.id,
            author_id=author.id,
            body="hello",
        )
        _db.session.add(comment)
        _db.session.commit()

        VoteService.upvote(voter.id, "comment", comment.id)

        events = ReputationService.list_public_events(author.id)
        assert events == [], "Comment votes never generate reputation events."

    def test_vote008_self_vote_raises_error(self, db_session):
        """Voting on own content raises VoteError; no reputation event emitted."""
        user = _make_user()
        post = _make_post(user, workspace_id=None)
        _db.session.commit()

        with pytest.raises(VoteError):
            VoteService.upvote(user.id, "post", post.id)

        events = ReputationService.list_public_events(user.id)
        assert events == []
