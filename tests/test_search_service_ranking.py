"""Service-level tests for search ranking integration.

Verifies that SearchService re-ranks post candidates correctly using the
weighted heuristic: title match rank > body-only match, freshness ordering,
and personalisation boosts (unread / stale-read).

Uses the SQLite LIKE back-end (``db_session`` fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.services.auth_service import AuthService
from backend.services.post_service import PostService
from backend.services.read_history_service import ReadHistoryService
from backend.services.search_service import SearchService

# ── Helpers ────────────────────────────────────────────────────────────────────

_ctr = {"n": 0}


def _uid() -> int:
    _ctr["n"] += 1
    return _ctr["n"]


def _make_author():
    n = _uid()
    return AuthService.register(
        f"rank{n}@test.com", f"rankauthor{n}", "StrongPass123!!"
    )


def _make_post(
    author_id: int, title: str, body: str = "", tags: list[str] | None = None
):
    post = PostService.create(author_id, title, body, tags=tags or [])
    return PostService.publish(post)


def _make_reader():
    n = _uid()
    return AuthService.register(
        f"reader{n}@test.com", f"rankreader{n}", "StrongPass123!!"
    )


# ── Title-match ranking ────────────────────────────────────────────────────────


class TestRankingTitleVsBody:
    def test_title_hit_ranks_above_body_only_hit(self, db_session):  # noqa: ARG002
        """A post whose *title* matches the query should rank above one that
        only matches in the body."""
        author = _make_author()
        body_match = _make_post(
            author.id, "Unrelated Topic", "This covers Python deeply."
        )
        title_match = _make_post(author.id, "Python Tutorial", "General intro post.")

        results = SearchService.search("Python")
        posts = results.posts
        assert len(posts) == 2
        ids = [p.id for p in posts]
        # Title-match post must appear before the body-match post.
        assert ids.index(title_match.id) < ids.index(body_match.id)

    def test_exact_title_ranks_above_partial_title(self, db_session):  # noqa: ARG002
        """Exact title match should rank above a partial title match."""
        author = _make_author()
        partial = _make_post(author.id, "Python Tips and Tricks")
        exact = _make_post(author.id, "Python")

        results = SearchService.search("Python")
        ids = [p.id for p in results.posts]
        assert ids.index(exact.id) < ids.index(partial.id)


# ── Freshness ranking ──────────────────────────────────────────────────────────


class TestRankingFreshness:
    def test_recently_updated_ranks_above_older_with_equal_title(self, db_session):  # noqa: ARG002
        """When title match is equal, the more recently updated post should rank first."""
        author = _make_author()
        # Both have the same title — same title score.
        old_post = _make_post(author.id, "Golang Guide", "Old content.")
        new_post = _make_post(author.id, "Golang Guide", "Newer content.")

        # Backdate old_post's published_at and updated_at so freshness differs.
        old_post.published_at = datetime.now(UTC) - timedelta(days=180)
        old_post.updated_at = datetime.now(UTC) - timedelta(days=180)
        _db.session.commit()

        results = SearchService.search("Golang Guide")
        ids = [p.id for p in results.posts]
        assert ids.index(new_post.id) < ids.index(old_post.id)


# ── Tag-match ranking ─────────────────────────────────────────────────────────


class TestRankingTagMatch:
    def test_post_with_matching_tag_ranks_above_tag_free_post(self, db_session):  # noqa: ARG002
        """A post tagged 'flask' should rank above an untagged post when
        searching for 'flask', assuming equal title relevance."""
        author = _make_author()
        no_tag = _make_post(
            author.id, "Web Framework Overview", "Learn about flask here."
        )
        tagged = _make_post(
            author.id, "Web Framework Overview", "Generic body.", tags=["flask"]
        )

        results = SearchService.search("flask")
        posts = results.posts
        # Both should appear; tagged one should be ranked first.
        ids = [p.id for p in posts]
        assert tagged.id in ids and no_tag.id in ids
        assert ids.index(tagged.id) < ids.index(no_tag.id)


# ── Personalisation — unread boost ────────────────────────────────────────────


class TestRankingUnreadBoost:
    def test_unread_post_ranks_above_read_post(self, db_session):  # noqa: ARG002
        """For an authenticated user, a never-read post should rank above a
        post the user has already read (all else equal)."""
        author = _make_author()
        reader = _make_reader()

        # Two equal posts (same title, same body, created at same time).
        already_read = _make_post(author.id, "Equal Interest Post", "Same content.")
        unread_post = _make_post(author.id, "Equal Interest Post", "Same content.")

        # Simulate user having read `already_read` but not `unread_post`.
        ReadHistoryService.record_read(reader.id, already_read)

        results = SearchService.search("Equal Interest", user_id=reader.id)
        ids = [p.id for p in results.posts]
        assert unread_post.id in ids and already_read.id in ids
        assert ids.index(unread_post.id) < ids.index(already_read.id)

    def test_anonymous_gets_no_unread_boost(self, db_session):  # noqa: ARG002
        """Anonymous search (user_id=None) should NOT apply unread boost."""
        author = _make_author()
        _make_post(author.id, "Anonymous Test Post A", "Content A.")
        _make_post(author.id, "Anonymous Test Post B", "Content B.")

        results_anon = SearchService.search("Anonymous Test Post", user_id=None)
        # Just verify it returns results without error.
        assert len(results_anon.posts) == 2


# ── Personalisation — stale-read boost ────────────────────────────────────────


class TestRankingStaleReadBoost:
    def test_stale_read_post_ranks_above_current_read(self, db_session):  # noqa: ARG002
        """A post the user read before its latest update should rank above one
        the user is fully up to date with (all else equal)."""
        author = _make_author()
        reader = _make_reader()

        up_to_date = _make_post(author.id, "Stale Test Post", "Version 1 content.")
        to_become_stale = _make_post(author.id, "Stale Test Post", "Version 1 content.")

        # User reads both posts at their current version (1).
        ReadHistoryService.record_read(reader.id, up_to_date)
        ReadHistoryService.record_read(reader.id, to_become_stale)

        # Update `to_become_stale` body → bumps version to 2.
        PostService.update(to_become_stale, markdown_body="Updated body — version 2.")
        _db.session.refresh(to_become_stale)

        # Reader's record still has last_read_version=1 for to_become_stale.
        results = SearchService.search("Stale Test Post", user_id=reader.id)
        ids = [p.id for p in results.posts]
        assert to_become_stale.id in ids and up_to_date.id in ids
        assert ids.index(to_become_stale.id) < ids.index(up_to_date.id)


# ── Sorting stability ──────────────────────────────────────────────────────────


class TestRankingSortStability:
    def test_sort_is_deterministic(self, db_session):  # noqa: ARG002
        """Searching twice returns the same order."""
        author = _make_author()
        _make_post(author.id, "Stable Sort Alpha", "Content about stability.")
        _make_post(author.id, "Stable Sort Beta", "More stable content.")

        r1 = SearchService.search("Stable Sort")
        r2 = SearchService.search("Stable Sort")
        assert [p.id for p in r1.posts] == [p.id for p in r2.posts]

    def test_draft_posts_excluded(self, db_session):  # noqa: ARG002
        """Draft posts must never appear in search results."""
        author = _make_author()
        PostService.create(author.id, "Secret Draft Post", "Hidden content.")
        # Do NOT publish — status stays 'draft'.
        results = SearchService.search("Secret Draft")
        assert results.post_total == 0
        assert results.posts == []
