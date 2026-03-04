"""Tests for Prompt Evolution Analytics — data integrity and correctness.

Coverage
--------
  DAI-001  Version timeline entries are ordered ascending by version_number.
  DAI-002  Timeline summary uses release note text when available.
  DAI-003  Timeline summary falls back to revision summary when no release note.
  DAI-004  AI-generated flag is set when revision has source_metadata_json with source=ai_suggestion.
  DAI-005  AI-generated flag is False for human (non-AI) revisions.
  DAI-006  Rating trend deltas are correctly computed per version.
  DAI-007  Rating trend vote_count is cumulative (not per-version).
  DAI-008  Fork vote counts reflect actual votes on fork posts.
  DAI-009  Unique readers count matches UserPostRead rows.
  DAI-010  get_version_timeline executes bounded number of SQL queries.
  DAI-011  Fork tree is ordered newest-first by created_at.
  DAI-012  Rating trend returns empty list when no timeline entries exist.
"""

from __future__ import annotations

import itertools
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import event as sa_event

from backend.extensions import db as _db
from backend.models.analytics import AnalyticsEvent
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.post_release_note import PostReleaseNote
from backend.models.post_version import PostVersion
from backend.models.revision import Revision, RevisionStatus
from backend.models.user_post_read import UserPostRead
from backend.models.vote import Vote
from backend.services import prompt_analytics_service as svc

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"dai{n}@example.com",
        username=f"daiuser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(
    author,
    *,
    workspace_id: int | None = None,
    status: PostStatus = PostStatus.published,
    view_count: int = 0,
) -> Post:
    n = _n()
    p = Post(
        title=f"DAI-Prompt {n}",
        slug=f"dai-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        view_count=view_count,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_post(
    author,
    *,
    workspace_id: int | None = None,
    status: PostStatus = PostStatus.published,
    created_at: datetime | None = None,
) -> Post:
    n = _n()
    p = Post(
        title=f"DAI-Post {n}",
        slug=f"dai-post-{n}",
        kind="article",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    if created_at is not None:
        p.created_at = created_at
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_revision(
    post: Post,
    author,
    *,
    summary: str = "Fix typo",
    source_metadata_json: dict | None = None,
    status: RevisionStatus = RevisionStatus.accepted,
) -> Revision:
    rev = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_number=1,
        proposed_markdown="updated body",
        summary=summary,
        status=status,
        source_metadata_json=source_metadata_json,
    )
    _db.session.add(rev)
    _db.session.flush()
    return rev


def _make_version(
    post: Post,
    version_number: int,
    *,
    revision: Revision | None = None,
    created_at: datetime | None = None,
) -> PostVersion:
    pv = PostVersion(
        post_id=post.id,
        version_number=version_number,
        markdown_body="body",
        accepted_by_id=post.author_id,
        revision_id=revision.id if revision else None,
    )
    if created_at is not None:
        pv.created_at = created_at
    _db.session.add(pv)
    _db.session.flush()
    return pv


def _make_release_note(
    post: Post,
    version_number: int,
    summary: str,
    revision: Revision | None = None,
) -> PostReleaseNote:
    rn = PostReleaseNote(
        post_id=post.id,
        version_number=version_number,
        summary=summary,
        accepted_revision_id=revision.id if revision else None,
        auto_generated=revision is None,
    )
    _db.session.add(rn)
    _db.session.flush()
    return rn


def _add_vote(post: Post, voter) -> Vote:
    v = Vote(
        user_id=voter.id,
        target_type="post",
        target_id=post.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(v)
    _db.session.flush()
    return v


def _add_vote_at(post: Post, voter, dt: datetime) -> Vote:
    v = Vote(
        user_id=voter.id,
        target_type="post",
        target_id=post.id,
        created_at=dt,
    )
    _db.session.add(v)
    _db.session.flush()
    return v


def _link_derived_from(from_post: Post, to_post: Post):
    link = ContentLink(
        from_post_id=from_post.id,
        to_post_id=to_post.id,
        link_type="derived_from",
    )
    _db.session.add(link)
    _db.session.flush()


@contextmanager
def _count_queries(db) -> Generator[dict, None, None]:
    counter: dict = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: PLR0913
        counter["n"] += 1

    engine = db.engine
    sa_event.listen(engine, "before_cursor_execute", _before)
    try:
        yield counter
    finally:
        sa_event.remove(engine, "before_cursor_execute", _before)


# ──────────────────────────────────────────────────────────────────────────────
# DAI-001  Version timeline order
# ──────────────────────────────────────────────────────────────────────────────


class TestVersionTimelineOrder:
    def test_entries_ordered_ascending(self, db_session):
        """DAI-001: timeline entries are sorted ascending by version_number."""
        author = _make_user()
        prompt = _make_prompt(author)
        _make_version(prompt, 1)
        _make_version(prompt, 2)
        _make_version(prompt, 3)
        _db.session.commit()

        timeline = svc.get_version_timeline(prompt, workspace_id=None)
        assert [e.version_number for e in timeline] == [1, 2, 3]


# ──────────────────────────────────────────────────────────────────────────────
# DAI-002  Release note summary preferred over revision summary
# ──────────────────────────────────────────────────────────────────────────────


class TestVersionTimelineSummarySource:
    def test_release_note_preferred(self, db_session):
        """DAI-002: release note text takes priority over revision summary."""
        author = _make_user()
        prompt = _make_prompt(author)
        rev = _make_revision(prompt, author, summary="Revision text")
        _make_version(prompt, 1, revision=rev)
        _make_release_note(prompt, 1, "Release note text", rev)
        _db.session.commit()

        timeline = svc.get_version_timeline(prompt, workspace_id=None)
        assert len(timeline) == 1
        assert timeline[0].summary == "Release note text"

    def test_revision_summary_fallback(self, db_session):
        """DAI-003: revision summary used when no release note exists."""
        author = _make_user()
        prompt = _make_prompt(author)
        rev = _make_revision(prompt, author, summary="My revision summary")
        _make_version(prompt, 1, revision=rev)
        _db.session.commit()

        timeline = svc.get_version_timeline(prompt, workspace_id=None)
        assert len(timeline) == 1
        assert timeline[0].summary == "My revision summary"

    def test_fallback_for_no_revision(self, db_session):
        """v1 with no linked revision gets 'Initial version' fallback."""
        author = _make_user()
        prompt = _make_prompt(author)
        _make_version(prompt, 1)  # no revision_id
        _db.session.commit()

        timeline = svc.get_version_timeline(prompt, workspace_id=None)
        assert timeline[0].summary == "Initial version"


# ──────────────────────────────────────────────────────────────────────────────
# DAI-004 / DAI-005  AI attribution flag
# ──────────────────────────────────────────────────────────────────────────────


class TestAIGeneratedFlag:
    def test_ai_source_sets_flag(self, db_session):
        """DAI-004: revision with source=ai_suggestion sets is_ai_generated=True."""
        author = _make_user()
        prompt = _make_prompt(author)
        rev = _make_revision(
            prompt,
            author,
            source_metadata_json={
                "source": "ai_suggestion",
                "ai_review_request_id": 7,
                "suggestion_id": "s-001",
            },
        )
        _make_version(prompt, 1, revision=rev)
        _db.session.commit()

        timeline = svc.get_version_timeline(prompt, workspace_id=None)
        assert timeline[0].is_ai_generated is True

    def test_human_revision_flag_false(self, db_session):
        """DAI-005: human revision (no source_metadata_json) yields is_ai_generated=False."""
        author = _make_user()
        prompt = _make_prompt(author)
        rev = _make_revision(prompt, author)  # no source_metadata_json
        _make_version(prompt, 1, revision=rev)
        _db.session.commit()

        timeline = svc.get_version_timeline(prompt, workspace_id=None)
        assert timeline[0].is_ai_generated is False


# ──────────────────────────────────────────────────────────────────────────────
# DAI-006 / DAI-007  Rating trend deltas
# ──────────────────────────────────────────────────────────────────────────────


class TestRatingTrend:
    def test_trend_empty_when_no_timeline(self, db_session):
        """DAI-012: rating trend is empty when no PostVersion rows exist."""
        author = _make_user()
        prompt = _make_prompt(author)
        _db.session.commit()

        trend = svc.get_rating_trend(prompt, workspace_id=None)
        assert trend == []

    def test_delta_is_zero_for_v1(self, db_session):
        """DAI-006a: v1 delta is always 0 (no previous version)."""
        author = _make_user()
        voter = _make_user()
        prompt = _make_prompt(author)
        t_v1 = datetime.now(UTC)
        pv = _make_version(prompt, 1)
        pv.created_at = t_v1
        _db.session.flush()
        # Add 2 votes after v1 creation
        _add_vote(prompt, voter)
        _add_vote(prompt, author)
        _db.session.commit()

        trend = svc.get_rating_trend(prompt, workspace_id=None)
        assert trend[0].version_number == 1
        assert trend[0].delta == 0  # no previous version

    def test_delta_reflects_votes_between_versions(self, db_session):
        """DAI-006b: delta for v2 = votes added between v1 and v2."""
        author = _make_user()
        v1_voter = _make_user()
        v2_voter = _make_user()
        prompt = _make_prompt(author)

        t_v1 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        t_v2 = datetime(2025, 2, 1, 12, 0, tzinfo=UTC)

        pv1 = _make_version(prompt, 1)
        pv1.created_at = t_v1
        _db.session.flush()

        pv2 = _make_version(prompt, 2)
        pv2.created_at = t_v2
        _db.session.flush()

        # Vote before v1 (captured in v1's cumulative)
        _add_vote_at(prompt, v1_voter, t_v1 - timedelta(hours=1))
        # Vote between v1 and v2 (captured only in v2's cumulative)
        _add_vote_at(prompt, v2_voter, t_v1 + timedelta(hours=1))
        _db.session.commit()

        trend = svc.get_rating_trend(prompt, workspace_id=None)
        assert len(trend) == 2
        v1_snap = next(s for s in trend if s.version_number == 1)
        v2_snap = next(s for s in trend if s.version_number == 2)

        # v1: 1 prior vote, delta=0 (no previous version)
        assert v1_snap.vote_count == 1
        assert v1_snap.delta == 0

        # v2: 2 cumulative votes, delta=1 (gained 1 between v1→v2)
        assert v2_snap.vote_count == 2
        assert v2_snap.delta == 1

    def test_vote_count_cumulative(self, db_session):
        """DAI-007: rating trend vote_count is cumulative, not per-version."""
        author = _make_user()
        voter1 = _make_user()
        voter2 = _make_user()
        voter3 = _make_user()
        prompt = _make_prompt(author)

        t_v1 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        t_v2 = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)

        pv1 = _make_version(prompt, 1)
        pv1.created_at = t_v1
        _db.session.flush()
        pv2 = _make_version(prompt, 2)
        pv2.created_at = t_v2
        _db.session.flush()

        # All 3 votes happen before v2 creation
        _add_vote_at(prompt, voter1, t_v1 - timedelta(hours=1))
        _add_vote_at(prompt, voter2, t_v1 + timedelta(hours=1))
        _add_vote_at(prompt, voter3, t_v2 - timedelta(hours=1))
        _db.session.commit()

        trend = svc.get_rating_trend(prompt, workspace_id=None)
        v2_snap = next(s for s in trend if s.version_number == 2)
        # All 3 votes existed before v2 creation → cumulative count = 3
        assert v2_snap.vote_count == 3


# ──────────────────────────────────────────────────────────────────────────────
# DAI-008  Fork vote counts
# ──────────────────────────────────────────────────────────────────────────────


class TestForkVoteCounts:
    def test_fork_vote_count_correct(self, db_session):
        """DAI-008: each fork entry's vote_count reflects actual vote rows."""
        author = _make_user()
        voter_a = _make_user()
        voter_b = _make_user()
        prompt = _make_prompt(author)
        fork_a = _make_post(author, status=PostStatus.published)
        fork_b = _make_post(author, status=PostStatus.published)
        _link_derived_from(fork_a, prompt)
        _link_derived_from(fork_b, prompt)
        _add_vote(fork_a, voter_a)
        _add_vote(fork_a, voter_b)
        _add_vote(fork_b, voter_a)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        votes_by_id = {f.post_id: f.vote_count for f in forks}
        assert votes_by_id[fork_a.id] == 2
        assert votes_by_id[fork_b.id] == 1

    def test_fork_with_no_votes_has_zero(self, db_session):
        """Fork with no votes shows vote_count=0."""
        author = _make_user()
        prompt = _make_prompt(author)
        fork = _make_post(author, status=PostStatus.published)
        _link_derived_from(fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert forks[0].vote_count == 0


# ──────────────────────────────────────────────────────────────────────────────
# DAI-009  Unique readers count
# ──────────────────────────────────────────────────────────────────────────────


class TestUniqueReadersCount:
    def test_unique_readers_count_correct(self, db_session):
        """DAI-009: unique_readers reflects UserPostRead count for the prompt."""
        author = _make_user()
        reader_a = _make_user()
        reader_b = _make_user()
        prompt = _make_prompt(author)
        for reader in (reader_a, reader_b):
            upr = UserPostRead(
                user_id=reader.id,
                post_id=prompt.id,
                last_read_at=datetime.now(UTC),
                last_read_version=1,
            )
            _db.session.add(upr)
        _db.session.commit()

        stats = svc.get_execution_stats(prompt, workspace_id=None)
        assert stats.unique_readers == 2

    def test_readers_from_other_posts_not_counted(self, db_session):
        """Readers of other posts don't inflate the count."""
        author = _make_user()
        reader = _make_user()
        prompt = _make_prompt(author)
        other = _make_prompt(author)
        upr = UserPostRead(
            user_id=reader.id,
            post_id=other.id,
            last_read_at=datetime.now(UTC),
            last_read_version=1,
        )
        _db.session.add(upr)
        _db.session.commit()

        stats = svc.get_execution_stats(prompt, workspace_id=None)
        assert stats.unique_readers == 0


# ──────────────────────────────────────────────────────────────────────────────
# DAI-010  Bounded query count
# ──────────────────────────────────────────────────────────────────────────────

_BOUNDED_QUERY_LIMIT = 12


class TestBoundedQueryCount:
    def test_version_timeline_bounded_queries(self, db_session):
        """DAI-010: get_version_timeline uses at most _BOUNDED_QUERY_LIMIT queries."""
        author = _make_user()
        voter = _make_user()
        prompt = _make_prompt(author)
        for i in range(1, 4):
            rev = _make_revision(prompt, author, summary=f"Change {i}")
            _make_version(prompt, i, revision=rev)
            _make_release_note(prompt, i, f"Release {i}", rev)
        _add_vote(prompt, voter)
        _db.session.commit()

        with _count_queries(_db) as counter:
            svc.get_version_timeline(prompt, workspace_id=None)
        assert counter["n"] <= _BOUNDED_QUERY_LIMIT

    def test_get_fork_tree_bounded_queries(self, db_session):
        """get_fork_tree uses at most _BOUNDED_QUERY_LIMIT queries."""
        author = _make_user()
        voter = _make_user()
        prompt = _make_prompt(author)
        for _ in range(5):
            fork = _make_post(author, status=PostStatus.published)
            _link_derived_from(fork, prompt)
            _add_vote(fork, voter)
        _db.session.commit()

        with _count_queries(_db) as counter:
            svc.get_fork_tree(prompt, workspace_id=None)
        assert counter["n"] <= _BOUNDED_QUERY_LIMIT

    def test_get_execution_stats_bounded_queries(self, db_session):
        """get_execution_stats uses at most _BOUNDED_QUERY_LIMIT queries."""
        from datetime import UTC  # noqa: PLC0415

        author = _make_user()
        reader = _make_user()
        prompt = _make_prompt(author, view_count=10)
        for i in range(3):
            ev = AnalyticsEvent(
                event_type="post_view",
                post_id=prompt.id,
                occurred_at=datetime.now(UTC) - timedelta(days=i),
            )
            _db.session.add(ev)
        upr = UserPostRead(
            user_id=reader.id,
            post_id=prompt.id,
            last_read_at=datetime.now(UTC),
            last_read_version=1,
        )
        _db.session.add(upr)
        _db.session.commit()

        with _count_queries(_db) as counter:
            svc.get_execution_stats(prompt, workspace_id=None)
        assert counter["n"] <= _BOUNDED_QUERY_LIMIT


# ──────────────────────────────────────────────────────────────────────────────
# DAI-011  Fork tree ordering
# ──────────────────────────────────────────────────────────────────────────────


class TestForkOrdering:
    def test_forks_ordered_newest_first(self, db_session):
        """DAI-011: fork tree is ordered descending by created_at."""
        author = _make_user()
        prompt = _make_prompt(author)
        older_fork = _make_post(
            author,
            status=PostStatus.published,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        newer_fork = _make_post(
            author,
            status=PostStatus.published,
            created_at=datetime(2025, 6, 1, tzinfo=UTC),
        )
        _link_derived_from(older_fork, prompt)
        _link_derived_from(newer_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert len(forks) == 2
        assert forks[0].post_id == newer_fork.id
        assert forks[1].post_id == older_fork.id
