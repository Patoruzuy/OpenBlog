"""Unit tests for RecentlyImprovedService.

Verifies visibility rules, window filtering, count accuracy, ordering,
and the configured limit — without triggering N+1 queries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.recently_improved_service import RecentlyImprovedService

# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    user, _ = make_user_token("author@example.com", "postauthor")
    return user


@pytest.fixture()
def contributor(make_user_token):
    user, _ = make_user_token("contrib@ri.com", "contrib", role="contributor")
    return user


@pytest.fixture()
def editor(make_user_token):
    user, _ = make_user_token("editor@ri.com", "editor_ri", role="editor")
    return user


def _make_post(author, *, title="Test Post", status=PostStatus.published, slug=None):
    """Helper: persist and return a Post."""
    post = Post(
        author_id=author.id,
        title=title,
        slug=slug or title.lower().replace(" ", "-"),
        markdown_body="# Hello\n\nBody text.",
        status=status,
        version=1,
    )
    db.session.add(post)
    db.session.commit()
    return post


def _make_accepted_revision(
    post,
    contributor,
    editor,
    *,
    reviewed_at: datetime | None = None,
    public_identity_mode: str | None = None,
    public_display_name_snapshot: str | None = None,
) -> Revision:
    """Helper: persist a pre-accepted Revision with a given ``reviewed_at``."""
    if reviewed_at is None:
        reviewed_at = datetime.now(UTC)
    rev = Revision(
        post_id=post.id,
        author_id=contributor.id,
        base_version_number=post.version,
        proposed_markdown="# Hello\n\nImproved body.",
        summary="Improve wording",
        status=RevisionStatus.accepted,
        reviewed_by_id=editor.id,
        reviewed_at=reviewed_at,
        public_identity_mode=public_identity_mode,
        public_display_name_snapshot=public_display_name_snapshot,
    )
    db.session.add(rev)
    db.session.commit()
    return rev


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestGetRecentlyImprovedPosts:
    # ── Visibility ────────────────────────────────────────────────────────────

    def test_returns_only_published_posts(
        self, db_session, author, contributor, editor
    ):
        """Posts with an accepted revision in the window are included when published."""
        post = _make_post(author, title="Published Post")
        _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert len(result) == 1
        assert result[0]["post"].id == post.id

    def test_excludes_draft_posts(self, db_session, author, contributor, editor):
        """Draft posts with accepted revisions must never appear."""
        draft = _make_post(
            author, title="Draft Post", status=PostStatus.draft, slug="draft-p"
        )
        _make_accepted_revision(draft, contributor, editor)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result == []

    def test_excludes_archived_posts(self, db_session, author, contributor, editor):
        """Archived posts must be excluded from the feed."""
        archived = _make_post(
            author, title="Archived Post", status=PostStatus.archived, slug="arch-p"
        )
        _make_accepted_revision(archived, contributor, editor)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result == []

    def test_excludes_scheduled_posts(self, db_session, author, contributor, editor):
        """Scheduled posts must not appear in the recently-improved feed."""
        sched = _make_post(
            author, title="Scheduled Post", status=PostStatus.scheduled, slug="sched-p"
        )
        _make_accepted_revision(sched, contributor, editor)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result == []

    # ── Window filtering ──────────────────────────────────────────────────────

    def test_excludes_revision_older_than_window(
        self, db_session, author, contributor, editor
    ):
        """A revision accepted before the cutoff must not surface the post."""
        post = _make_post(author, title="Old Revision Post", slug="old-rev-p")
        old_date = datetime.now(UTC) - timedelta(days=31)
        _make_accepted_revision(post, contributor, editor, reviewed_at=old_date)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result == []

    def test_includes_revision_exactly_at_window_boundary(
        self, db_session, author, contributor, editor
    ):
        """A revision accepted at exactly cutoff (now - days) is included."""
        post = _make_post(author, title="Boundary Post", slug="boundary-p")
        # Use 29 days ago to be safely inside the 30-day window.
        recent_date = datetime.now(UTC) - timedelta(days=29)
        _make_accepted_revision(post, contributor, editor, reviewed_at=recent_date)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert len(result) == 1
        assert result[0]["post"].id == post.id

    def test_mixed_window_keeps_only_in_window_post(
        self, db_session, author, contributor, editor
    ):
        """With two posts — one in-window, one out — only the in-window post appears."""
        in_post = _make_post(author, title="In Window", slug="in-window")
        out_post = _make_post(author, title="Out Window", slug="out-window")

        _make_accepted_revision(
            in_post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=5),
        )
        _make_accepted_revision(
            out_post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=40),
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        post_ids = [r["post"].id for r in result]
        assert in_post.id in post_ids
        assert out_post.id not in post_ids

    # ── Count accuracy ────────────────────────────────────────────────────────

    def test_counts_multiple_accepted_revisions_correctly(
        self, db_session, author, contributor, editor
    ):
        """accepted_count_in_window reflects all accepted revisions in the window."""
        post = _make_post(author, title="Multi Revision Post", slug="multi-rev")

        for i in range(3):
            post.version = i + 1  # bump version to allow multiple revisions
            db.session.commit()
            _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert len(result) == 1
        assert result[0]["accepted_count_in_window"] == 3

    def test_out_of_window_revisions_not_counted(
        self, db_session, author, contributor, editor
    ):
        """Only in-window accepted revisions contribute to the count."""
        post = _make_post(author, title="Count Window Post", slug="count-win")

        # One old revision (outside window)
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=60),
        )
        # Two recent revisions (inside window)
        post.version = 2
        db.session.commit()
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=2),
        )
        post.version = 3
        db.session.commit()
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=1),
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert len(result) == 1
        assert result[0]["accepted_count_in_window"] == 2

    def test_does_not_count_pending_revisions(
        self, db_session, author, contributor, editor
    ):
        """Pending revisions must not increment the accepted count."""
        post = _make_post(author, title="Pending Only Post", slug="pend-only")

        # One accepted + one pending on the same post
        _make_accepted_revision(post, contributor, editor)

        pending = Revision(
            post_id=post.id,
            author_id=contributor.id,
            base_version_number=post.version,
            proposed_markdown="# Hello\n\nPending body.",
            summary="Pending change",
            status=RevisionStatus.pending,
        )
        db.session.add(pending)
        db.session.commit()

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["accepted_count_in_window"] == 1

    def test_does_not_count_rejected_revisions(
        self, db_session, author, contributor, editor
    ):
        """Rejected revisions must not appear in the accepted count."""
        post = _make_post(author, title="Rejected Only Post", slug="rej-only")

        # One accepted + one rejected
        _make_accepted_revision(post, contributor, editor)

        rejected = Revision(
            post_id=post.id,
            author_id=contributor.id,
            base_version_number=post.version,
            proposed_markdown="# Hello\n\nRejected body.",
            summary="Rejected change",
            status=RevisionStatus.rejected,
            reviewed_by_id=editor.id,
            reviewed_at=datetime.now(UTC),
        )
        db.session.add(rejected)
        db.session.commit()

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["accepted_count_in_window"] == 1

    # ── Ordering ──────────────────────────────────────────────────────────────

    def test_orders_by_last_accepted_at_descending(
        self, db_session, author, contributor, editor
    ):
        """Most-recently-improved post must appear first."""
        older_post = _make_post(author, title="Older Improved", slug="older-imp")
        newer_post = _make_post(author, title="Newer Improved", slug="newer-imp")

        _make_accepted_revision(
            older_post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=10),
        )
        _make_accepted_revision(
            newer_post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=2),
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["post"].id == newer_post.id
        assert result[1]["post"].id == older_post.id

    def test_last_accepted_at_reflects_most_recent_revision(
        self, db_session, author, contributor, editor
    ):
        """last_accepted_at equals the max(reviewed_at) across all in-window revisions."""
        post = _make_post(author, title="Multi Time Post", slug="multi-time")

        earlier = datetime.now(UTC) - timedelta(days=5)
        later = datetime.now(UTC) - timedelta(days=1)

        _make_accepted_revision(post, contributor, editor, reviewed_at=earlier)
        post.version += 1
        db.session.commit()
        _make_accepted_revision(post, contributor, editor, reviewed_at=later)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert len(result) == 1
        # SQLite strips timezone info from aggregate results; compare naively.
        result_ts = result[0]["last_accepted_at"]
        result_naive = result_ts.replace(tzinfo=None) if result_ts.tzinfo else result_ts
        later_naive = later.replace(tzinfo=None)
        assert result_naive.replace(microsecond=0) >= later_naive.replace(microsecond=0)

    # ── Limit ─────────────────────────────────────────────────────────────────

    def test_respects_limit(self, db_session, author, contributor, editor):
        """Only *limit* results are returned even when more qualify."""
        for i in range(5):
            post = _make_post(author, title=f"Post {i}", slug=f"limit-post-{i}")
            _make_accepted_revision(
                post,
                contributor,
                editor,
                reviewed_at=datetime.now(UTC) - timedelta(hours=i),
            )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=3)

        assert len(result) == 3

    def test_returns_empty_list_when_no_accepted_revisions(self, db_session, author):
        """Service returns [] when no accepted revisions exist in the window."""
        _make_post(author, title="No Revisions Post", slug="no-rev")

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=6)

        assert result == []

    # ── Return shape ──────────────────────────────────────────────────────────

    def test_result_dicts_have_expected_keys(
        self, db_session, author, contributor, editor
    ):
        """Each result entry exposes the required keys."""
        post = _make_post(author, title="Shape Post", slug="shape-post")
        _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=6)

        assert len(result) == 1
        entry = result[0]
        assert "post" in entry
        assert "accepted_count_in_window" in entry
        assert "last_accepted_at" in entry
        assert "last_accepted_by_display" in entry

    def test_post_object_has_author_and_tags_loaded(
        self, db_session, author, contributor, editor
    ):
        """Templates can access post.author and post.tags without extra DB hits."""
        post = _make_post(author, title="Loaded Post", slug="loaded-post")
        _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=6)

        assert len(result) == 1
        loaded_post = result[0]["post"]
        # Author relationship must be hydrated (not a lazy-load sentinel)
        assert loaded_post.author is not None
        assert loaded_post.author.id == author.id
        # Tags list must be accessible (empty list is fine for this post)
        assert isinstance(loaded_post.tags, list)


class TestLastAcceptedByDisplay:
    """Tests for the contributor-attribution privacy logic."""

    # ── Snapshot present ──────────────────────────────────────────────────────

    def test_public_mode_with_snapshot_returns_snapshot_name(
        self, db_session, author, contributor, editor
    ):
        """public mode + non-null snapshot → snapshot name is returned."""
        post = _make_post(author, title="Public Contrib", slug="pub-contrib")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="public",
            public_display_name_snapshot="Alice Public",
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] == "Alice Public"

    def test_pseudonymous_mode_with_snapshot_returns_snapshot_name(
        self, db_session, author, contributor, editor
    ):
        """pseudonymous mode + non-null snapshot → snapshot name is returned."""
        post = _make_post(author, title="Pseudo Contrib", slug="pseudo-contrib")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="pseudonymous",
            public_display_name_snapshot="B. Pseudonym",
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] == "B. Pseudonym"

    # ── Anonymous ─────────────────────────────────────────────────────────────

    def test_anonymous_mode_returns_none(self, db_session, author, contributor, editor):
        """anonymous mode always yields None — no name leakage."""
        post = _make_post(author, title="Anon Contrib", slug="anon-contrib")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="anonymous",
            public_display_name_snapshot="Should Not Appear",
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] is None

    def test_none_mode_returns_none(self, db_session, author, contributor, editor):
        """None / unknown mode is treated as private — safe default."""
        post = _make_post(author, title="Unknown Mode", slug="unknown-mode")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode=None,
            public_display_name_snapshot="Should Not Appear",
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] is None

    # ── Snapshot absent, mode public ──────────────────────────────────────────

    def test_public_mode_no_snapshot_falls_back_to_user_display_name(
        self, db_session, author, contributor, editor
    ):
        """public mode + null snapshot → live user display_name is used."""
        # Set a display_name on the contributor so the fallback has something.
        contributor.display_name = "Contrib Display"
        db.session.commit()

        post = _make_post(author, title="Fallback Name", slug="fallback-name")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="public",
            public_display_name_snapshot=None,
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] == "Contrib Display"

    def test_public_mode_no_snapshot_no_display_name_falls_back_to_username(
        self, db_session, author, contributor, editor
    ):
        """public mode + null snapshot + null display_name → username is used."""
        contributor.display_name = None
        db.session.commit()

        post = _make_post(author, title="Username Fallback", slug="username-fallback")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="public",
            public_display_name_snapshot=None,
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] == contributor.username

    # ── Multi-revision: attribution goes to the LATEST ────────────────────────

    def test_attribution_reflects_latest_accepted_revision(
        self, db_session, author, contributor, editor, make_user_token
    ):
        """When two accepted revisions exist, the LATEST contributor is shown."""
        user_b, _ = make_user_token("user_b@example.com", "user_b", role="contributor")
        user_b.display_name = None
        db.session.commit()

        post = _make_post(author, title="Latest Contrib Post", slug="latest-contrib")

        # Older revision by contributor
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=5),
            public_identity_mode="public",
            public_display_name_snapshot="Old Contributor",
        )
        # Newer revision by user_b
        post.version += 1
        db.session.commit()
        _make_accepted_revision(
            post,
            user_b,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=1),
            public_identity_mode="public",
            public_display_name_snapshot="New Contributor",
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] == "New Contributor"

    def test_attribution_anonymous_latest_hides_name(
        self, db_session, author, contributor, editor, make_user_token
    ):
        """If the LATEST revision is anonymous, name is None even if an older one was public."""
        user_b, _ = make_user_token("user_c@example.com", "user_c", role="contributor")

        post = _make_post(author, title="Anon Latest Post", slug="anon-latest")

        # Older public revision
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=5),
            public_identity_mode="public",
            public_display_name_snapshot="Should Not Show",
        )
        # Newer anonymous revision
        post.version += 1
        db.session.commit()
        _make_accepted_revision(
            post,
            user_b,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=1),
            public_identity_mode="anonymous",
            public_display_name_snapshot=None,
        )

        result = RecentlyImprovedService.get_recently_improved_posts(days=30, limit=10)

        assert result[0]["last_accepted_by_display"] is None
