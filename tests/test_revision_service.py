"""Tests for RevisionService."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.revision import RevisionStatus
from backend.services.revision_service import RevisionError, RevisionService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    user, _ = make_user_token("author@example.com", "postauthor")
    return user


@pytest.fixture()
def contributor(make_user_token):
    user, _ = make_user_token("contrib@example.com", "contrib", role="contributor")
    return user


@pytest.fixture()
def editor(make_user_token):
    user, _ = make_user_token("editor@example.com", "editor", role="editor")
    return user


@pytest.fixture()
def pub_post(author):
    post = Post(
        author_id=author.id,
        title="Original Post",
        slug="original-post",
        markdown_body="# Hello\n\nOriginal body text.",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


@pytest.fixture()
def pending_revision(contributor, pub_post):
    return RevisionService.submit(
        post_id=pub_post.id,
        author_id=contributor.id,
        proposed_markdown="# Hello\n\nImproved body text with fixes.",
        summary="Fix typos and improve phrasing",
    )


# ── submit ────────────────────────────────────────────────────────────────────


class TestSubmit:
    def test_returns_pending_revision(self, contributor, pub_post):
        rev = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nCompletely new content here.",
            summary="Rewrote the intro",
        )
        assert rev.id is not None
        assert rev.status == RevisionStatus.pending
        assert rev.post_id == pub_post.id
        assert rev.author_id == contributor.id

    def test_captures_base_version_number(self, contributor, pub_post):
        rev = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nNew content.",
            summary="Update",
        )
        assert rev.base_version_number == pub_post.version

    def test_diff_cache_populated(self, contributor, pub_post):
        rev = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nNew content.",
            summary="Update",
        )
        assert rev.diff_cache is not None
        assert "---" in rev.diff_cache
        assert "+++" in rev.diff_cache

    def test_post_not_found_raises_404(self, contributor):
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.submit(
                post_id=99999,
                author_id=contributor.id,
                proposed_markdown="# New",
                summary="Update",
            )
        assert exc_info.value.status_code == 404

    def test_draft_post_raises_400(self, contributor, author):
        draft = Post(
            author_id=author.id,
            slug="draft-no-revision",
            title="Draft",
            markdown_body="# Draft",
            status=PostStatus.draft,
        )
        db.session.add(draft)
        db.session.commit()

        with pytest.raises(RevisionError) as exc_info:
            RevisionService.submit(
                post_id=draft.id,
                author_id=contributor.id,
                proposed_markdown="# Changed",
                summary="Update",
            )
        assert exc_info.value.status_code == 400

    def test_author_self_submit_raises_400(self, author, pub_post):
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.submit(
                post_id=pub_post.id,
                author_id=author.id,
                proposed_markdown="# Hello\n\nAuthor changed this.",
                summary="Author's own edit",
            )
        assert exc_info.value.status_code == 400

    def test_identical_markdown_raises_400(self, contributor, pub_post):
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.submit(
                post_id=pub_post.id,
                author_id=contributor.id,
                proposed_markdown=pub_post.markdown_body,
                summary="No change",
            )
        assert exc_info.value.status_code == 400

    def test_blank_summary_raises_400(self, contributor, pub_post):
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.submit(
                post_id=pub_post.id,
                author_id=contributor.id,
                proposed_markdown="# Hello\n\nNew content.",
                summary="   ",
            )
        assert exc_info.value.status_code == 400

    def test_base_version_id_none_when_no_versions(self, contributor, pub_post):
        """When no PostVersion exists yet, base_version_id should be None."""
        rev = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nChanged.",
            summary="First revision",
        )
        assert rev.base_version_id is None

    def test_base_version_id_set_when_versions_exist(
        self, contributor, pub_post, editor
    ):
        """After an accepted revision, subsequent submissions capture base_version_id."""
        first = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nVersion 2 content.",
            summary="First revision",
        )
        RevisionService.accept(first.id, reviewer_id=editor.id)
        db.session.expire(pub_post)

        second = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nVersion 3 content.",
            summary="Second revision",
        )
        assert second.base_version_id is not None


# ── get_diff ──────────────────────────────────────────────────────────────────


class TestGetDiff:
    def test_returns_unified_diff_string(self, pending_revision):
        diff = RevisionService.get_diff(pending_revision.id)
        assert diff.startswith("---")
        assert "+++" in diff

    def test_not_found_raises_404(self, db_session):
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.get_diff(99999)
        assert exc_info.value.status_code == 404

    def test_recomputes_when_cache_cleared(self, pending_revision):
        pending_revision.diff_cache = None
        db.session.commit()

        diff = RevisionService.get_diff(pending_revision.id)
        assert diff  # non-empty

        # Cache should be repopulated.
        db.session.expire(pending_revision)
        assert pending_revision.diff_cache is not None


# ── accept ────────────────────────────────────────────────────────────────────


class TestAccept:
    def test_status_becomes_accepted(self, pending_revision, editor):
        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(pending_revision)
        assert pending_revision.status == RevisionStatus.accepted

    def test_post_body_updated(self, pending_revision, pub_post, editor):
        proposed = pending_revision.proposed_markdown
        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(pub_post)
        assert pub_post.markdown_body == proposed

    def test_post_version_incremented(self, pending_revision, pub_post, editor):
        original_version = pub_post.version
        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(pub_post)
        assert pub_post.version == original_version + 1

    def test_post_version_snapshot_created(self, pending_revision, pub_post, editor):
        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(pub_post)
        snapshot = db.session.scalar(
            __import__("sqlalchemy", fromlist=["select"]).select(PostVersion).where(
                PostVersion.post_id == pub_post.id,
                PostVersion.version_number == pub_post.version,
            )
        )
        assert snapshot is not None
        assert snapshot.revision_id == pending_revision.id
        assert snapshot.accepted_by_id == editor.id

    def test_contributor_gains_reputation(self, pending_revision, contributor, editor):
        before = contributor.reputation_score or 0
        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(contributor)
        assert contributor.reputation_score == before + RevisionService.ACCEPT_REPUTATION

    def test_notification_sent_to_contributor(
        self, pending_revision, contributor, editor
    ):
        from sqlalchemy import select

        from backend.models.notification import Notification

        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == contributor.id,
                Notification.notification_type == "revision_accepted",
            )
        )
        assert notif is not None
        assert notif.is_read is False

    def test_reviewer_id_recorded(self, pending_revision, editor):
        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(pending_revision)
        assert pending_revision.reviewed_by_id == editor.id
        assert pending_revision.reviewed_at is not None

    def test_not_found_raises_404(self, editor):
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.accept(99999, reviewer_id=editor.id)
        assert exc_info.value.status_code == 404

    def test_already_accepted_raises_400(self, pending_revision, editor):
        RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.accept(pending_revision.id, reviewer_id=editor.id)
        assert exc_info.value.status_code == 400


# ── reject ────────────────────────────────────────────────────────────────────


class TestReject:
    def test_status_becomes_rejected(self, pending_revision, editor):
        RevisionService.reject(pending_revision.id, reviewer_id=editor.id, note="")
        db.session.expire(pending_revision)
        assert pending_revision.status == RevisionStatus.rejected

    def test_rejection_note_stored(self, pending_revision, editor):
        RevisionService.reject(
            pending_revision.id, reviewer_id=editor.id, note="Needs more detail."
        )
        db.session.expire(pending_revision)
        assert pending_revision.rejection_note == "Needs more detail."

    def test_blank_note_stored_as_none(self, pending_revision, editor):
        RevisionService.reject(pending_revision.id, reviewer_id=editor.id, note="  ")
        db.session.expire(pending_revision)
        assert pending_revision.rejection_note is None

    def test_post_body_unchanged(self, pending_revision, pub_post, editor):
        original_body = pub_post.markdown_body
        RevisionService.reject(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(pub_post)
        assert pub_post.markdown_body == original_body

    def test_post_version_unchanged(self, pending_revision, pub_post, editor):
        original_version = pub_post.version
        RevisionService.reject(pending_revision.id, reviewer_id=editor.id)
        db.session.expire(pub_post)
        assert pub_post.version == original_version

    def test_notification_sent_to_contributor(
        self, pending_revision, contributor, editor
    ):
        from sqlalchemy import select

        from backend.models.notification import Notification

        RevisionService.reject(
            pending_revision.id, reviewer_id=editor.id, note="Not suitable."
        )
        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == contributor.id,
                Notification.notification_type == "revision_rejected",
            )
        )
        assert notif is not None
        assert "Not suitable." in (notif.body or "")

    def test_not_found_raises_404(self, editor):
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.reject(99999, reviewer_id=editor.id)
        assert exc_info.value.status_code == 404

    def test_already_rejected_raises_400(self, pending_revision, editor):
        RevisionService.reject(pending_revision.id, reviewer_id=editor.id)
        with pytest.raises(RevisionError) as exc_info:
            RevisionService.reject(pending_revision.id, reviewer_id=editor.id)
        assert exc_info.value.status_code == 400


# ── list_for_post ─────────────────────────────────────────────────────────────


class TestListForPost:
    def test_returns_all_revisions(self, contributor, pub_post, editor):
        RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nFirst change.",
            summary="First",
        )
        revisions, total = RevisionService.list_for_post(pub_post.id)
        assert total == 1
        assert len(revisions) == 1

    def test_status_filter(self, contributor, pub_post, editor):
        rev = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nAnother change.",
            summary="Change",
        )
        RevisionService.accept(rev.id, reviewer_id=editor.id)

        pending, total_pending = RevisionService.list_for_post(
            pub_post.id, status=RevisionStatus.pending
        )
        accepted, total_accepted = RevisionService.list_for_post(
            pub_post.id, status=RevisionStatus.accepted
        )
        assert total_pending == 0
        assert total_accepted == 1
        assert len(accepted) == 1


# ── list_pending ──────────────────────────────────────────────────────────────


class TestListPending:
    def test_returns_only_pending(self, contributor, pub_post, editor, author):
        # Create a second post for variety.
        post2 = Post(
            author_id=author.id,
            slug="second-post",
            title="Second Post",
            markdown_body="# Second",
            status=PostStatus.published,
        )
        db.session.add(post2)
        db.session.commit()

        rev1 = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contributor.id,
            proposed_markdown="# Hello\n\nChange for post 1.",
            summary="Post 1 fix",
        )
        RevisionService.submit(
            post_id=post2.id,
            author_id=contributor.id,
            proposed_markdown="# Second\n\nAdded content.",
            summary="Post 2 addition",
        )
        # Accept rev1 so only post2 revision remains pending.
        RevisionService.accept(rev1.id, reviewer_id=editor.id)

        pending, total = RevisionService.list_pending()
        assert total == 1
        assert all(r.status == RevisionStatus.pending for r in pending)
