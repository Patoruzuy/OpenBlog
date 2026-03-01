"""Integration tests for the "Recently improved" section on the homepage.

Verifies:
- Section heading renders when accepted revisions exist.
- Post titles and revision metadata appear correctly.
- Empty state renders when no qualifying revisions exist.
- Draft and unpublished post titles never appear.
- Section heading is present even with empty data.
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
        slug=slug or title.lower().replace(" ", "-"),
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


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    user, _ = make_user_token("author@home-ri.com", "author_ri")
    return user


@pytest.fixture()
def contributor(make_user_token):
    user, _ = make_user_token("contrib@home-ri.com", "contrib_ri", role="contributor")
    return user


@pytest.fixture()
def editor(make_user_token):
    user, _ = make_user_token("editor@home-ri.com", "editor_ri2", role="editor")
    return user


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRecentlyImprovedSection:
    def test_section_heading_always_present(self, auth_client, db_session):
        """'Recently improved' heading renders regardless of data."""
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b"Recently improved" in resp.data

    def test_section_subtitle_always_present(self, auth_client, db_session):
        """Subtitle blurb is rendered on the homepage."""
        resp = auth_client.get("/")
        assert b"accepted community revisions" in resp.data

    def test_shows_post_title_when_accepted_revision_exists(
        self, auth_client, db_session, author, contributor, editor
    ):
        """Post title appears in the recently-improved section."""
        post = _make_post(author, title="Brilliant Article", slug="brilliant-article")
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/")
        assert b"Brilliant Article" in resp.data

    def test_shows_accepted_revisions_count(
        self, auth_client, db_session, author, contributor, editor
    ):
        """'Accepted revisions:' metadata line is rendered per entry."""
        post = _make_post(author, title="Counted Post", slug="counted-post")
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/")
        assert b"Accepted revisions:" in resp.data

    def test_improved_badge_rendered(
        self, auth_client, db_session, author, contributor, editor
    ):
        """The 'Improved' badge appears next to the post title."""
        post = _make_post(author, title="Badge Post", slug="badge-post")
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/")
        assert b"Improved" in resp.data

    def test_empty_state_when_no_accepted_revisions(self, auth_client, db_session):
        """Empty state message renders when there are no qualified revisions."""
        resp = auth_client.get("/")
        assert b"No recently improved posts yet." in resp.data

    def test_empty_state_has_browse_posts_link(self, auth_client, db_session):
        """Empty state includes a 'Browse posts' CTA."""
        resp = auth_client.get("/")
        assert b"Browse posts" in resp.data

    def test_draft_post_title_excluded(
        self, auth_client, db_session, author, contributor, editor
    ):
        """A draft post's title must never appear in the recently-improved section."""
        draft = _make_post(
            author,
            title="Secret Draft XYZ",
            status=PostStatus.draft,
            slug="secret-draft",
        )
        _make_accepted_revision(draft, contributor, editor)

        resp = auth_client.get("/")
        assert b"Secret Draft XYZ" not in resp.data

    def test_archived_post_title_excluded(
        self, auth_client, db_session, author, contributor, editor
    ):
        """An archived post must not appear in the recently-improved feed."""
        archived = _make_post(
            author,
            title="Hidden Archived XYZ",
            status=PostStatus.archived,
            slug="hidden-archived",
        )
        _make_accepted_revision(archived, contributor, editor)

        resp = auth_client.get("/")
        assert b"Hidden Archived XYZ" not in resp.data

    def test_out_of_window_revision_produces_empty_state(
        self, auth_client, db_session, author, contributor, editor
    ):
        """A revision older than 30 days does not populate the feed."""
        post = _make_post(author, title="Ancient Post", slug="ancient-post")
        old_date = datetime.now(UTC) - timedelta(days=45)
        _make_accepted_revision(post, contributor, editor, reviewed_at=old_date)

        resp = auth_client.get("/")
        assert b"No recently improved posts yet." in resp.data

    def test_multiple_qualifying_posts_all_appear(
        self, auth_client, db_session, author, contributor, editor
    ):
        """All qualifying posts up to the limit are rendered."""
        posts = []
        for i in range(3):
            p = _make_post(author, title=f"Improved Post {i}", slug=f"imp-post-{i}")
            _make_accepted_revision(
                p,
                contributor,
                editor,
                reviewed_at=datetime.now(UTC) - timedelta(hours=i),
            )
            posts.append(p)

        resp = auth_client.get("/")
        for p in posts:
            assert p.title.encode() in resp.data

    def test_response_is_200(
        self, auth_client, db_session, author, contributor, editor
    ):
        """Homepage must return HTTP 200 with the new section present."""
        post = _make_post(author, title="Health Check Post", slug="health-post")
        _make_accepted_revision(post, contributor, editor)

        resp = auth_client.get("/")
        assert resp.status_code == 200


class TestRecentlyImprovedAttribution:
    """Template tests for the 'Last improved by \u2026' attribution segment."""

    def test_attribution_shown_when_public_mode(
        self, auth_client, db_session, author, contributor, editor
    ):
        """Contributor name renders when public_identity_mode is 'public'."""
        post = _make_post(author, title="Public Attribution Post", slug="pub-attr")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="public",
            public_display_name_snapshot="Jane Public",
        )

        resp = auth_client.get("/")
        assert b"Jane Public" in resp.data

    def test_attribution_shown_when_pseudonymous_mode(
        self, auth_client, db_session, author, contributor, editor
    ):
        """Pseudonym renders when public_identity_mode is 'pseudonymous'."""
        post = _make_post(author, title="Pseudo Attribution Post", slug="pseudo-attr")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="pseudonymous",
            public_display_name_snapshot="X. Pseudonym",
        )

        resp = auth_client.get("/")
        assert b"X. Pseudonym" in resp.data

    def test_attribution_hidden_when_anonymous_mode(
        self, auth_client, db_session, author, contributor, editor
    ):
        """No name appears when public_identity_mode is 'anonymous'."""
        post = _make_post(author, title="Anon Attribution Post", slug="anon-attr")
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode="anonymous",
            public_display_name_snapshot="Must Not Appear XYZ",
        )

        resp = auth_client.get("/")
        assert b"Must Not Appear XYZ" not in resp.data

    def test_attribution_hidden_when_mode_none(
        self, auth_client, db_session, author, contributor, editor
    ):
        """No name appears when public_identity_mode is None (default)."""
        post = _make_post(author, title="No Mode Attribution Post", slug="no-mode-attr")
        # Snapshot present but mode is None — must be suppressed
        _make_accepted_revision(
            post,
            contributor,
            editor,
            public_identity_mode=None,
            public_display_name_snapshot="Also Must Not Appear XYZ",
        )

        resp = auth_client.get("/")
        assert b"Also Must Not Appear XYZ" not in resp.data
