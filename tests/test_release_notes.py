"""Tests for the post release-note / changelog feature.

Covers:
  - Accepting a revision creates a PostReleaseNote
  - The release note carries the correct version_number and summary
  - Rejecting a revision does NOT create a release note
  - Multiple accepted revisions produce multiple notes in descending order
  - get_post_release_notes returns notes in version-descending order
  - Post detail page renders the Changelog section when notes exist
  - Changelog section is absent when no notes exist
  - Each changelog entry includes a "View diff" link (except v1)
  - Draft posts cannot expose their changelog via the public route
"""

from __future__ import annotations

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.post_release_note import PostReleaseNote
from backend.models.user import User, UserRole
from backend.services.release_notes_service import get_post_release_notes
from backend.services.revision_service import RevisionService

# ── Helpers ───────────────────────────────────────────────────────────────────


def _user(email: str, username: str, role: str = "contributor") -> User:
    u = User(email=email, username=username, password_hash="x", role=UserRole(role))
    db.session.add(u)
    db.session.commit()
    return u


def _post(author: User, *, slug: str, version: int = 1) -> Post:
    p = Post(
        author_id=author.id,
        title=f"Post {slug}",
        slug=slug,
        markdown_body="# Original body.",
        status=PostStatus.published,
        version=version,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _accept(
    post: Post, contrib: User, editor: User, *, summary: str, body: str | None = None
) -> RevisionService:
    body = body or f"# Updated\n\nBody for: {summary}"
    rev = RevisionService.submit(
        post_id=post.id,
        author_id=contrib.id,
        proposed_markdown=body,
        summary=summary,
    )
    return RevisionService.accept(rev.id, editor.id)


# ── Service-level tests ───────────────────────────────────────────────────────


class TestCreateReleaseNoteOnAccept:
    def test_accepting_revision_creates_release_note(self, db_session):
        author = _user("rns1a@x.test", "rns1a")
        contrib = _user("rns1c@x.test", "rns1c")
        ed = _user("rns1e@x.test", "rns1e", role="editor")
        _accept(_post(author, slug="rns-post-1"), contrib, ed, summary="Initial update")

        notes = (
            db.session.query(PostReleaseNote)
            .filter_by(
                post_id=db.session.query(Post).filter_by(slug="rns-post-1").first().id
            )
            .all()
        )
        assert len(notes) == 1

    def test_release_note_has_correct_version_number(self, db_session):
        author = _user("rns2a@x.test", "rns2a")
        contrib = _user("rns2c@x.test", "rns2c")
        ed = _user("rns2e@x.test", "rns2e", role="editor")
        post = _post(author, slug="rns-post-2")
        old_version = post.version
        _accept(post, contrib, ed, summary="Version bump")

        db.session.refresh(post)
        note = db.session.query(PostReleaseNote).filter_by(post_id=post.id).first()
        assert note is not None
        assert note.version_number == old_version + 1
        assert note.version_number == post.version

    def test_release_note_summary_matches_revision_summary(self, db_session):
        author = _user("rns3a@x.test", "rns3a")
        contrib = _user("rns3c@x.test", "rns3c")
        ed = _user("rns3e@x.test", "rns3e", role="editor")
        post = _post(author, slug="rns-post-3")
        _accept(post, contrib, ed, summary="Clarify introduction section")

        note = db.session.query(PostReleaseNote).filter_by(post_id=post.id).first()
        assert note is not None
        assert note.summary == "Clarify introduction section"

    def test_release_note_auto_generated_is_false(self, db_session):
        author = _user("rns4a@x.test", "rns4a")
        contrib = _user("rns4c@x.test", "rns4c")
        ed = _user("rns4e@x.test", "rns4e", role="editor")
        post = _post(author, slug="rns-post-4")
        _accept(post, contrib, ed, summary="Some update")

        note = db.session.query(PostReleaseNote).filter_by(post_id=post.id).first()
        assert note is not None
        assert note.auto_generated is False

    def test_release_note_links_to_revision(self, db_session):
        author = _user("rns5a@x.test", "rns5a")
        contrib = _user("rns5c@x.test", "rns5c")
        ed = _user("rns5e@x.test", "rns5e", role="editor")
        post = _post(author, slug="rns-post-5")
        rev = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown="# Other body",
            summary="Linked revision test",
        )
        revision_id = rev.id
        RevisionService.accept(rev.id, ed.id)

        note = db.session.query(PostReleaseNote).filter_by(post_id=post.id).first()
        assert note is not None
        assert note.accepted_revision_id == revision_id

    def test_release_note_has_created_at(self, db_session):
        author = _user("rns6a@x.test", "rns6a")
        contrib = _user("rns6c@x.test", "rns6c")
        ed = _user("rns6e@x.test", "rns6e", role="editor")
        post = _post(author, slug="rns-post-6")
        _accept(post, contrib, ed, summary="Timestamp test")

        note = db.session.query(PostReleaseNote).filter_by(post_id=post.id).first()
        assert note is not None
        assert note.created_at is not None


class TestNoReleaseNoteOnReject:
    def test_rejecting_revision_does_not_create_release_note(self, db_session):
        author = _user("rnr1a@x.test", "rnr1a")
        contrib = _user("rnr1c@x.test", "rnr1c")
        ed = _user("rnr1e@x.test", "rnr1e", role="editor")
        post = _post(author, slug="reject-post-1")

        rev = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown="# Rejected body",
            summary="This will be rejected",
        )
        RevisionService.reject(rev.id, ed.id, "Not suitable.")

        notes = db.session.query(PostReleaseNote).filter_by(post_id=post.id).all()
        assert len(notes) == 0


class TestMultipleRevisions:
    def test_each_accepted_revision_creates_one_note(self, db_session):
        author = _user("rnm1a@x.test", "rnm1a")
        ed = _user("rnm1e@x.test", "rnm1e", role="editor")
        post = _post(author, slug="multi-post-1")

        for i, summary in enumerate(("Add more detail", "Fix code example"), start=1):
            contrib = _user(f"rnm1c{i}@x.test", f"rnm1c{i}")
            _accept(post, contrib, ed, summary=summary, body=f"# Body {i}")

        notes = db.session.query(PostReleaseNote).filter_by(post_id=post.id).all()
        assert len(notes) == 2

    def test_version_numbers_are_sequential(self, db_session):
        author = _user("rnm2a@x.test", "rnm2a")
        ed = _user("rnm2e@x.test", "rnm2e", role="editor")
        post = _post(author, slug="multi-post-2")
        original_version = post.version

        for i, summary in enumerate(("First update", "Second update"), start=1):
            contrib = _user(f"rnm2c{i}@x.test", f"rnm2c{i}")
            _accept(post, contrib, ed, summary=summary, body=f"# Body {i}")

        notes = (
            db.session.query(PostReleaseNote)
            .filter_by(post_id=post.id)
            .order_by(PostReleaseNote.version_number)
            .all()
        )
        assert len(notes) == 2
        assert notes[0].version_number == original_version + 1
        assert notes[1].version_number == original_version + 2


class TestGetPostReleaseNotes:
    def test_returns_empty_list_for_post_with_no_revisions(self, db_session):
        author = _user("rng1a@x.test", "rng1a")
        post = _post(author, slug="get-post-1")
        assert get_post_release_notes(post.id) == []

    def test_returns_notes_in_version_descending_order(self, db_session):
        author = _user("rng2a@x.test", "rng2a")
        ed = _user("rng2e@x.test", "rng2e", role="editor")
        post = _post(author, slug="get-post-2")

        for i, summary in enumerate(("First", "Second", "Third"), start=1):
            contrib = _user(f"rng2c{i}@x.test", f"rng2c{i}")
            _accept(post, contrib, ed, summary=summary, body=f"# Body {i}")

        notes = get_post_release_notes(post.id)
        assert len(notes) == 3
        assert (
            notes[0].version_number > notes[1].version_number > notes[2].version_number
        )

    def test_returns_summaries_in_version_desc_order(self, db_session):
        author = _user("rng3a@x.test", "rng3a")
        ed = _user("rng3e@x.test", "rng3e", role="editor")
        post = _post(author, slug="get-post-3")

        contrib1 = _user("rng3c1@x.test", "rng3c1")
        contrib2 = _user("rng3c2@x.test", "rng3c2")
        _accept(post, contrib1, ed, summary="Alpha", body="# Body Alpha")
        _accept(post, contrib2, ed, summary="Beta", body="# Body Beta")

        notes = get_post_release_notes(post.id)
        assert notes[0].summary == "Beta"
        assert notes[1].summary == "Alpha"


# ── Route/template-level tests ────────────────────────────────────────────────


class TestChangelogOnDetailPage:
    def test_no_changelog_when_no_release_notes(self, client, db_session):
        """Changelog section absent when post has no accepted revisions."""
        author = _user("rnt1a@x.test", "rnt1a")
        post = _post(author, slug="tpl-post-1")
        resp = client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200
        assert b'<section class="changelog' not in resp.data

    def test_changelog_section_appears_after_accepted_revision(
        self, client, db_session
    ):
        author = _user("rnt2a@x.test", "rnt2a")
        contrib = _user("rnt2c@x.test", "rnt2c")
        ed = _user("rnt2e@x.test", "rnt2e", role="editor")
        post = _post(author, slug="tpl-post-2")
        _accept(post, contrib, ed, summary="Improve readability")

        resp = client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200
        assert b'<section class="changelog' in resp.data

    def test_changelog_shows_version_and_summary(self, client, db_session):
        author = _user("rnt3a@x.test", "rnt3a")
        contrib = _user("rnt3c@x.test", "rnt3c")
        ed = _user("rnt3e@x.test", "rnt3e", role="editor")
        post = _post(author, slug="tpl-post-3")
        _accept(post, contrib, ed, summary="Add new config section")

        body = client.get(f"/posts/{post.slug}").data.decode()
        assert "Add new config section" in body
        db.session.refresh(post)
        assert f"v{post.version}" in body

    def test_changelog_view_diff_link_present(self, client, db_session):
        """Each entry for version > 1 has a 'View diff' compare link."""
        author = _user("rnt4a@x.test", "rnt4a")
        contrib = _user("rnt4c@x.test", "rnt4c")
        ed = _user("rnt4e@x.test", "rnt4e", role="editor")
        post = _post(author, slug="tpl-post-4")
        _accept(post, contrib, ed, summary="Revision with diff link")

        body = client.get(f"/posts/{post.slug}").data.decode()
        assert "View diff" in body
        assert "/compare?" in body

    def test_compare_link_uses_correct_version_range(self, client, db_session):
        """Compare link for vN points to from=N-1&to=N."""
        author = _user("rnt5a@x.test", "rnt5a")
        contrib = _user("rnt5c@x.test", "rnt5c")
        ed = _user("rnt5e@x.test", "rnt5e", role="editor")
        post = _post(author, slug="tpl-post-5", version=1)
        _accept(post, contrib, ed, summary="Version two", body="# v2 body")

        body = client.get(f"/posts/{post.slug}").data.decode()
        assert "from=1" in body
        assert "to=2" in body

    def test_draft_post_changelog_not_accessible_anonymously(self, client, db_session):
        """Anonymous users get 404 for draft posts, so changelog never leaks."""
        author = _user("rnt6a@x.test", "rnt6a")
        p = Post(
            author_id=author.id,
            title="Draft No Changelog",
            slug="tpl-draft-post",
            markdown_body="# Draft",
            status=PostStatus.draft,
        )
        db.session.add(p)
        db.session.commit()
        assert client.get(f"/posts/{p.slug}").status_code == 404
