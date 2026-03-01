"""Tests for RecentlyImprovedService.list_improvements (paginated listing).

Verifies:
- Only published posts are returned.
- The ``days`` cutoff filter is applied correctly.
- ``days=None`` (all-time) includes older improvements.
- Ordering: last_accepted_at DESC, with stable tie-break by post_id DESC.
- Pagination: correct total, pages, and per-page slicing.
- Attribution follows _resolve_display privacy rules.
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
    user, _ = make_user_token("imp_author@example.com", "imp_author")
    return user


@pytest.fixture()
def contributor(make_user_token):
    user, _ = make_user_token(
        "imp_contrib@example.com", "imp_contrib", role="contributor"
    )
    return user


@pytest.fixture()
def editor(make_user_token):
    user, _ = make_user_token("imp_editor@example.com", "imp_editor", role="editor")
    return user


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_post(author, *, title="Test Post", status=PostStatus.published, slug=None):
    post = Post(
        author_id=author.id,
        title=title,
        slug=slug or title.lower().replace(" ", "-").replace(":", ""),
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


# ── TestListImprovementsVisibility ────────────────────────────────────────────


class TestListImprovementsVisibility:
    def test_returns_published_posts(self, db_session, author, contributor, editor):
        post = _make_post(author, title="Published A", slug="pub-a")
        _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["total"] == 1
        assert result["items"][0]["post"].id == post.id

    def test_excludes_draft_posts(self, db_session, author, contributor, editor):
        draft = _make_post(
            author, title="Draft B", slug="draft-b", status=PostStatus.draft
        )
        _make_accepted_revision(draft, contributor, editor)

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["total"] == 0
        assert result["items"] == []

    def test_excludes_archived_posts(self, db_session, author, contributor, editor):
        archived = _make_post(
            author, title="Archived C", slug="archived-c", status=PostStatus.archived
        )
        _make_accepted_revision(archived, contributor, editor)

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["total"] == 0

    def test_excludes_scheduled_posts(self, db_session, author, contributor, editor):
        sched = _make_post(
            author, title="Scheduled D", slug="scheduled-d", status=PostStatus.scheduled
        )
        _make_accepted_revision(sched, contributor, editor)

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["total"] == 0


# ── TestListImprovementsWindowFilter ─────────────────────────────────────────


class TestListImprovementsWindowFilter:
    def test_includes_revision_within_window(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Recent E", slug="recent-e")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=5),
        )

        result = RecentlyImprovedService.list_improvements(days=7)

        assert result["total"] == 1

    def test_excludes_revision_outside_window(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Old F", slug="old-f")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=45),
        )

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["total"] == 0

    def test_days_none_includes_all_time(self, db_session, author, contributor, editor):
        post = _make_post(author, title="Ancient G", slug="ancient-g")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=500),
        )

        result = RecentlyImprovedService.list_improvements(days=None)

        assert result["total"] == 1
        assert result["items"][0]["post"].id == post.id

    def test_days_none_excludes_nothing(self, db_session, author, contributor, editor):
        """With days=None both old and recent improvements appear."""
        recent = _make_post(author, title="Recent H", slug="recent-h")
        old = _make_post(author, title="Ancient H", slug="ancient-h")
        _make_accepted_revision(
            recent,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=3),
        )
        _make_accepted_revision(
            old,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=400),
        )

        result = RecentlyImprovedService.list_improvements(days=None)

        post_ids = {e["post"].id for e in result["items"]}
        assert recent.id in post_ids
        assert old.id in post_ids


# ── TestListImprovementsOrdering ──────────────────────────────────────────────


class TestListImprovementsOrdering:
    def test_ordered_by_last_accepted_at_desc(
        self, db_session, author, contributor, editor
    ):
        earlier = _make_post(author, title="Earlier I", slug="earlier-i")
        later = _make_post(author, title="Later I", slug="later-i")

        now = datetime.now(UTC)
        _make_accepted_revision(
            earlier, contributor, editor, reviewed_at=now - timedelta(days=5)
        )
        _make_accepted_revision(
            later, contributor, editor, reviewed_at=now - timedelta(days=1)
        )

        result = RecentlyImprovedService.list_improvements(days=30)

        items = result["items"]
        assert len(items) == 2
        assert items[0]["post"].id == later.id
        assert items[1]["post"].id == earlier.id

    def test_stable_tiebreak_by_post_id_desc(
        self, db_session, author, contributor, editor
    ):
        """When two posts share the same reviewed_at, higher post_id comes first."""
        same_time = datetime.now(UTC) - timedelta(hours=2)

        post_a = _make_post(author, title="Post A Tie", slug="post-a-tie")
        post_b = _make_post(author, title="Post B Tie", slug="post-b-tie")

        _make_accepted_revision(post_a, contributor, editor, reviewed_at=same_time)
        _make_accepted_revision(post_b, contributor, editor, reviewed_at=same_time)

        result = RecentlyImprovedService.list_improvements(days=30)

        items = result["items"]
        assert len(items) == 2
        # Higher post_id goes first (DESC tie-break)
        assert items[0]["post"].id > items[1]["post"].id

    def test_accepted_count_reflects_revisions_in_window(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Multi J", slug="multi-j")
        now = datetime.now(UTC)
        _make_accepted_revision(
            post, contributor, editor, reviewed_at=now - timedelta(days=2)
        )
        _make_accepted_revision(
            post, contributor, editor, reviewed_at=now - timedelta(days=1)
        )

        result = RecentlyImprovedService.list_improvements(days=7)

        assert result["items"][0]["accepted_count_in_window"] == 2

    def test_last_accepted_at_is_most_recent(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Last K", slug="last-k")
        now = datetime.now(UTC)
        earlier_dt = now - timedelta(days=5)
        later_dt = now - timedelta(days=1)
        _make_accepted_revision(post, contributor, editor, reviewed_at=earlier_dt)
        _make_accepted_revision(post, contributor, editor, reviewed_at=later_dt)

        result = RecentlyImprovedService.list_improvements(days=30)

        last_at = result["items"][0]["last_accepted_at"]
        # Strip tzinfo for comparison (SQLite returns naive datetimes from MAX())
        last_at_naive = last_at.replace(tzinfo=None) if last_at.tzinfo else last_at
        later_naive = later_dt.replace(tzinfo=None)
        assert abs((last_at_naive - later_naive).total_seconds()) < 2


# ── TestListImprovementsPagination ────────────────────────────────────────────


class TestListImprovementsPagination:
    def test_total_counts_all_matching_posts(
        self, db_session, author, contributor, editor
    ):
        for i in range(5):
            post = _make_post(author, title=f"Post L{i}", slug=f"post-l{i}")
            _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.list_improvements(days=30, page=1, per_page=3)

        assert result["total"] == 5
        assert result["pages"] == 2

    def test_page1_returns_first_slice(self, db_session, author, contributor, editor):
        now = datetime.now(UTC)
        posts = []
        for i in range(4):
            p = _make_post(author, title=f"Slice M{i}", slug=f"slice-m{i}")
            _make_accepted_revision(
                p,
                contributor,
                editor,
                reviewed_at=now - timedelta(days=i),
            )
            posts.append(p)

        result = RecentlyImprovedService.list_improvements(days=30, page=1, per_page=2)

        assert len(result["items"]) == 2
        assert result["page"] == 1
        # Most recently improved first: posts[0] then posts[1]
        assert result["items"][0]["post"].id == posts[0].id
        assert result["items"][1]["post"].id == posts[1].id

    def test_page2_returns_second_slice(self, db_session, author, contributor, editor):
        now = datetime.now(UTC)
        posts = []
        for i in range(4):
            p = _make_post(author, title=f"Slice N{i}", slug=f"slice-n{i}")
            _make_accepted_revision(
                p,
                contributor,
                editor,
                reviewed_at=now - timedelta(days=i),
            )
            posts.append(p)

        result = RecentlyImprovedService.list_improvements(days=30, page=2, per_page=2)

        assert len(result["items"]) == 2
        assert result["page"] == 2
        assert result["items"][0]["post"].id == posts[2].id
        assert result["items"][1]["post"].id == posts[3].id

    def test_empty_result_when_no_data(self, db_session, author):  # noqa: ARG002
        result = RecentlyImprovedService.list_improvements(days=30, page=1, per_page=20)

        assert result["total"] == 0
        assert result["items"] == []
        assert result["pages"] == 1

    def test_single_post_still_returns_shape(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Single O", slug="single-o")
        _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.list_improvements(days=30, page=1, per_page=20)

        assert result["total"] == 1
        assert result["pages"] == 1
        assert len(result["items"]) == 1
        assert result["per_page"] == 20

    def test_page_clamped_to_max(self, db_session, author, contributor, editor):
        """Requesting a page beyond the last page returns the last page."""
        post = _make_post(author, title="Single P", slug="single-p")
        _make_accepted_revision(post, contributor, editor)

        result = RecentlyImprovedService.list_improvements(
            days=30, page=99, per_page=20
        )

        assert result["page"] == 1
        assert len(result["items"]) == 1


# ── TestListImprovementsAttribution ──────────────────────────────────────────


class TestListImprovementsAttribution:
    def test_public_mode_returns_display_name(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Attr Q1", slug="attr-q1")
        contributor.display_name = "Attr Person"
        db.session.commit()
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="public",
        )

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["items"][0]["last_accepted_by_display"] == "Attr Person"

    def test_public_snapshot_takes_priority(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Attr Q2", slug="attr-q2")
        contributor.display_name = "Live Name"
        db.session.commit()
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="public",
            public_display_name_snapshot="Snapshot Name",
        )

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["items"][0]["last_accepted_by_display"] == "Snapshot Name"

    def test_pseudonymous_mode_returns_display_name(
        self, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Attr Q3", slug="attr-q3")
        contributor.display_name = "Pseudo Name"
        db.session.commit()
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="pseudonymous",
        )

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["items"][0]["last_accepted_by_display"] == "Pseudo Name"

    def test_anonymous_mode_returns_none(self, db_session, author, contributor, editor):
        post = _make_post(author, title="Attr Q4", slug="attr-q4")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="anonymous",
        )

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["items"][0]["last_accepted_by_display"] is None

    def test_none_mode_returns_none(self, db_session, author, contributor, editor):
        post = _make_post(author, title="Attr Q5", slug="attr-q5")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode=None,
        )

        result = RecentlyImprovedService.list_improvements(days=30)

        assert result["items"][0]["last_accepted_by_display"] is None
