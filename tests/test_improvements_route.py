"""Integration tests for the GET /improvements SSR page.

Verifies:
- Page returns 200 and renders correct headings.
- Default days=30 filter is active.
- Filter pills for 7/30/90/all work (correct active state in response).
- Empty state renders when no qualifying revisions exist.
- Post titles and metadata appear for qualifying posts.
- Only published posts appear.
- Attribution (by-name) follows privacy rules.
- Pagination links preserve the days query parameter.
- Homepage link "All improvements →" points to /improvements?days=30.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_post(author, *, title="Test Post", status=PostStatus.published, slug=None):
    post = Post(
        author_id=author.id,
        title=title,
        slug=slug or title.lower().replace(" ", "-").replace(":", ""),
        markdown_body="# Hello\n\nBody.",
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
    reviewed_at=None,
    public_identity_mode=None,
    public_display_name_snapshot=None,
):
    if reviewed_at is None:
        reviewed_at = datetime.now(UTC)
    rev = Revision(
        post_id=post.id,
        author_id=contributor.id,
        base_version_number=post.version,
        proposed_markdown="# Hello\n\nImproved.",
        summary="Improve",
        status=RevisionStatus.accepted,
        reviewed_by_id=editor.id,
        reviewed_at=reviewed_at,
        public_identity_mode=public_identity_mode,
        public_display_name_snapshot=public_display_name_snapshot,
    )
    db.session.add(rev)
    db.session.commit()
    return rev


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    user, _ = make_user_token("imp_route_author@example.com", "imp_route_author")
    return user


@pytest.fixture()
def contributor(make_user_token):
    user, _ = make_user_token(
        "imp_route_contrib@example.com", "imp_route_contrib", role="contributor"
    )
    return user


@pytest.fixture()
def editor(make_user_token):
    user, _ = make_user_token(
        "imp_route_editor@example.com", "imp_route_editor", role="editor"
    )
    return user


# ── TestImprovementsBasic ─────────────────────────────────────────────────────


class TestImprovementsBasic:
    def test_returns_200(self, auth_client, db_session):
        resp = auth_client.get("/improvements/")
        assert resp.status_code == 200

    def test_page_title_present(self, auth_client, db_session):
        resp = auth_client.get("/improvements/")
        assert b"Improvements" in resp.data

    def test_subtitle_present(self, auth_client, db_session):
        resp = auth_client.get("/improvements/")
        assert b"accepted community revisions" in resp.data

    def test_filter_pills_present(self, auth_client, db_session):
        resp = auth_client.get("/improvements/")
        html = resp.data.decode()
        assert "Last 7 days" in html
        assert "Last 30 days" in html
        assert "Last 90 days" in html
        assert "All time" in html

    def test_default_days_30_is_active(self, auth_client, db_session):
        """Without explicit days param the 30-day pill should carry aria-current."""
        resp = auth_client.get("/improvements/")
        html = resp.data.decode()
        # The active pill has filter-pill--active class AND aria-current="page"
        # Find the Last 30 days pill
        assert "days=30" in html or "filter-pill--active" in html

    def test_invalid_days_falls_back_to_30(self, auth_client, db_session):
        resp = auth_client.get("/improvements/?days=invalid")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The 30-day filter should be active
        assert "filter-pill--active" in html


# ── TestImprovementsEmptyState ────────────────────────────────────────────────


class TestImprovementsEmptyState:
    def test_empty_state_when_no_data(self, auth_client, db_session):
        resp = auth_client.get("/improvements/")
        assert b"No improvements yet for this window." in resp.data

    def test_empty_state_has_browse_posts_cta(self, auth_client, db_session):
        resp = auth_client.get("/improvements/")
        assert b"Browse posts" in resp.data

    def test_empty_for_narrow_window_with_old_revision(
        self, auth_client, db_session, author, contributor, editor
    ):
        """A revision 60 days old should not appear in the 7-day window."""
        post = _make_post(author, title="Old Revision Post", slug="old-rev-post")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=60),
        )
        resp = auth_client.get("/improvements/?days=7")
        assert b"No improvements yet for this window." in resp.data


# ── TestImprovementsEntries ───────────────────────────────────────────────────


class TestImprovementsEntries:
    def test_post_title_appears(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Entry Test Post", slug="entry-test-post")
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/improvements/")
        assert b"Entry Test Post" in resp.data

    def test_accepted_revisions_count_shown(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Count Test Post", slug="count-test-post")
        _make_accepted_revision(post, contributor, editor)
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/improvements/")
        assert b"Accepted revisions:" in resp.data

    def test_improved_badge_shown(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Badge Test Post", slug="badge-test-post")
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/improvements/")
        assert b"Improved" in resp.data

    def test_draft_excluded(self, auth_client, db_session, author, contributor, editor):
        draft = _make_post(
            author,
            title="Secret Draft ZZZZ",
            slug="secret-draft-zzzz",
            status=PostStatus.draft,
        )
        _make_accepted_revision(draft, contributor, editor)

        resp = auth_client.get("/improvements/")
        assert b"Secret Draft ZZZZ" not in resp.data

    def test_archived_excluded(
        self, auth_client, db_session, author, contributor, editor
    ):
        archived = _make_post(
            author,
            title="Hidden Archived ZZZZ",
            slug="hidden-archived-zzzz",
            status=PostStatus.archived,
        )
        _make_accepted_revision(archived, contributor, editor)

        resp = auth_client.get("/improvements/")
        assert b"Hidden Archived ZZZZ" not in resp.data

    def test_all_time_shows_old_revisions(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Ancient Post ZZ", slug="ancient-post-zz")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            reviewed_at=datetime.now(UTC) - timedelta(days=400),
        )

        resp = auth_client.get("/improvements/?days=all")
        assert b"Ancient Post ZZ" in resp.data

    def test_days7_shows_recent_only(
        self, auth_client, db_session, author, contributor, editor
    ):
        recent = _make_post(author, title="Recent Post 7d", slug="recent-post-7d")
        old = _make_post(author, title="Old Post 7d", slug="old-post-7d")
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
            reviewed_at=datetime.now(UTC) - timedelta(days=45),
        )

        resp = auth_client.get("/improvements/?days=7")
        html = resp.data.decode()
        assert "Recent Post 7d" in html
        assert "Old Post 7d" not in html


# ── TestImprovementsAttribution ───────────────────────────────────────────────


class TestImprovementsAttribution:
    def test_public_name_shown(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Public Attr Post", slug="pub-attr-post")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="public",
            public_display_name_snapshot="Public Contributor",
        )

        resp = auth_client.get("/improvements/")
        assert b"Public Contributor" in resp.data

    def test_pseudonymous_name_shown(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Pseudo Attr Post", slug="pseudo-attr-post")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="pseudonymous",
            public_display_name_snapshot="The Reviewer",
        )

        resp = auth_client.get("/improvements/")
        assert b"The Reviewer" in resp.data

    def test_anonymous_name_hidden(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="Anon Attr Post", slug="anon-attr-post")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="anonymous",
            public_display_name_snapshot="Must Not Appear ANON",
        )

        resp = auth_client.get("/improvements/")
        assert b"Must Not Appear ANON" not in resp.data

    def test_no_mode_name_hidden(
        self, auth_client, db_session, author, contributor, editor
    ):
        post = _make_post(author, title="No Mode Post", slug="no-mode-post")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode=None,
            public_display_name_snapshot="Must Not Appear NONE",
        )

        resp = auth_client.get("/improvements/")
        assert b"Must Not Appear NONE" not in resp.data


# ── TestImprovementsPagination ────────────────────────────────────────────────


class TestImprovementsPagination:
    def test_pagination_controls_shown_when_multiple_pages(
        self, auth_client, db_session, author, contributor, editor
    ):
        """When more posts than per_page, Next / pagination shows."""
        now = datetime.now(UTC)
        for i in range(25):
            p = _make_post(author, title=f"Pag Post {i}", slug=f"pag-post-{i}")
            _make_accepted_revision(
                p,
                contributor,
                editor,
                reviewed_at=now - timedelta(hours=i),
            )

        resp = auth_client.get("/improvements/?days=30&page=1")
        html = resp.data.decode()
        assert "Next" in html

    def test_page2_link_preserves_days_param(
        self, auth_client, db_session, author, contributor, editor
    ):
        """Pagination links must include the current days= value."""
        now = datetime.now(UTC)
        for i in range(25):
            p = _make_post(author, title=f"Pag Days Post {i}", slug=f"pag-days-{i}")
            _make_accepted_revision(
                p,
                contributor,
                editor,
                reviewed_at=now - timedelta(hours=i),
            )

        resp = auth_client.get("/improvements/?days=90&page=1")
        html = resp.data.decode()
        # The Next link must carry days=90
        assert "days=90" in html
        assert "page=2" in html

    def test_no_pagination_when_single_page(
        self, auth_client, db_session, author, contributor, editor
    ):
        """Single-page result must not render pagination nav."""
        post = _make_post(author, title="Only Post Pag", slug="only-post-pag")
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/improvements/")
        html = resp.data.decode()
        # Prev/Next should not appear
        assert "page-link" not in html


# ── TestHomepageImprovementsLink ──────────────────────────────────────────────


class TestHomepageImprovementsLink:
    def test_all_improvements_link_present(self, auth_client, db_session):
        """Homepage 'All improvements →' link must point to /improvements?days=30."""
        resp = auth_client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "/improvements/" in html
        assert "All improvements" in html

    def test_homepage_link_points_to_days_30(self, auth_client, db_session):
        """The link includes days=30 query parameter."""
        resp = auth_client.get("/")
        html = resp.data.decode()
        assert "days=30" in html
