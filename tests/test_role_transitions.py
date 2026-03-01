"""Tests for automatic role-promotion logic.

Covers:
- Reader → Contributor on first published post (PostService.publish)
- Reader → Contributor on first accepted revision (RevisionService.accept)
- Contributor role is NOT changed on subsequent publishes
- Authenticated readers can reach GET /posts/new (no longer gated by require_role)
- Anonymous users are still redirected to login from /posts/new
- Drafts page CTA button reflects email-verification state
"""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.user import UserRole


# ── Helpers ────────────────────────────────────────────────────────────────────


def _login(client, user_id: int) -> None:
    """Inject a Flask session cookie to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _make_published_post(author) -> Post:
    """Insert a published post for *author* directly (bypasses service)."""
    post = Post(
        author_id=author.id,
        title="Seed Post",
        slug="seed-post",
        markdown_body="# Seed\n\nContent here.",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── Role promotion via PostService.publish ─────────────────────────────────────


class TestReaderPromotedOnPublish:
    def test_reader_promoted_to_contributor_on_first_publish(
        self, make_user_token, db_session  # noqa: ARG002
    ):
        from backend.services.post_service import PostService

        user, _ = make_user_token("reader1@test.com", "reader1", role="reader")
        assert user.role == UserRole.reader

        post = PostService.create(user.id, "My First Post", "Hello world.")
        PostService.publish(post)

        db.session.refresh(user)
        assert user.role == UserRole.contributor

    def test_reader_role_unchanged_when_saving_draft(
        self, make_user_token, db_session  # noqa: ARG002
    ):
        from backend.services.post_service import PostService

        user, _ = make_user_token("reader2@test.com", "reader2", role="reader")
        PostService.create(user.id, "Draft Post", "Draft content.")

        db.session.refresh(user)
        assert user.role == UserRole.reader

    def test_contributor_role_unchanged_on_subsequent_publish(
        self, make_user_token, db_session  # noqa: ARG002
    ):
        from backend.services.post_service import PostService

        user, _ = make_user_token("contrib1@test.com", "contrib1", role="contributor")
        assert user.role == UserRole.contributor

        post = PostService.create(user.id, "Contrib Post", "Contributor content.")
        PostService.publish(post)

        db.session.refresh(user)
        assert user.role == UserRole.contributor

    def test_editor_role_unchanged_on_publish(
        self, make_user_token, db_session  # noqa: ARG002
    ):
        from backend.services.post_service import PostService

        user, _ = make_user_token("editor1@test.com", "editor1", role="editor")
        post = PostService.create(user.id, "Editor Post", "Editor content.")
        PostService.publish(post)

        db.session.refresh(user)
        assert user.role == UserRole.editor

    def test_second_publish_does_not_double_promote(
        self, make_user_token, db_session  # noqa: ARG002
    ):
        """After first publish promotes reader → contributor, second publish leaves them as contributor."""
        from backend.services.post_service import PostService

        user, _ = make_user_token("reader3@test.com", "reader3", role="reader")

        post1 = PostService.create(user.id, "Post One", "Content one.")
        PostService.publish(post1)
        db.session.refresh(user)
        assert user.role == UserRole.contributor  # promoted

        post2 = PostService.create(user.id, "Post Two", "Content two.")
        PostService.publish(post2)
        db.session.refresh(user)
        assert user.role == UserRole.contributor  # unchanged


# ── Role promotion via RevisionService.accept ─────────────────────────────────


class TestReaderPromotedOnAcceptedRevision:
    @pytest.fixture()
    def author(self, make_user_token):
        user, _ = make_user_token("postauthor@rt.com", "postauthor_rt", role="editor")
        return user

    @pytest.fixture()
    def pub_post(self, author):
        return _make_published_post(author)

    def test_reader_promoted_when_revision_accepted(
        self, make_user_token, author, pub_post, db_session  # noqa: ARG002
    ):
        from backend.services.revision_service import RevisionService

        reader, _ = make_user_token("rev_reader@test.com", "rev_reader", role="reader")
        assert reader.role == UserRole.reader

        revision = RevisionService.submit(
            post_id=pub_post.id,
            author_id=reader.id,
            proposed_markdown="# New\n\nImproved content.",
            summary="Fix wording",
        )
        RevisionService.accept(revision.id, reviewer_id=author.id)

        db.session.refresh(reader)
        assert reader.role == UserRole.contributor

    def test_contributor_not_demoted_when_revision_accepted(
        self, make_user_token, author, pub_post, db_session  # noqa: ARG002
    ):
        from backend.services.revision_service import RevisionService

        contrib, _ = make_user_token(
            "contrib_rev@test.com", "contrib_rev", role="contributor"
        )

        revision = RevisionService.submit(
            post_id=pub_post.id,
            author_id=contrib.id,
            proposed_markdown="# New\n\nImproved content from contrib.",
            summary="Better phrasing",
        )
        RevisionService.accept(revision.id, reviewer_id=author.id)

        db.session.refresh(contrib)
        assert contrib.role == UserRole.contributor

    def test_reputation_still_awarded_alongside_promotion(
        self, make_user_token, author, pub_post, db_session  # noqa: ARG002
    ):
        from backend.services.revision_service import RevisionService

        reader, _ = make_user_token(
            "rep_reader@test.com", "rep_reader", role="reader"
        )
        before = reader.reputation_score or 0

        revision = RevisionService.submit(
            post_id=pub_post.id,
            author_id=reader.id,
            proposed_markdown="# New\n\nLots of new content here.",
            summary="Major rewrite",
        )
        RevisionService.accept(revision.id, reviewer_id=author.id)

        db.session.refresh(reader)
        assert (reader.reputation_score or 0) > before
        assert reader.role == UserRole.contributor


# ── SSR route access: /posts/new ───────────────────────────────────────────────


class TestNewPostRouteAccess:
    def test_anonymous_redirected_to_login(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/posts/new")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_authenticated_reader_can_access(
        self, auth_client, make_user_token, db_session  # noqa: ARG002
    ):
        user, _ = make_user_token("reader_new@test.com", "reader_new", role="reader")
        _login(auth_client, user.id)
        resp = auth_client.get("/posts/new")
        assert resp.status_code == 200

    def test_contributor_can_access(
        self, auth_client, make_user_token, db_session  # noqa: ARG002
    ):
        user, _ = make_user_token("contrib_new@test.com", "contrib_new", role="contributor")
        _login(auth_client, user.id)
        resp = auth_client.get("/posts/new")
        assert resp.status_code == 200


# ── SSR drafts page CTA gating ─────────────────────────────────────────────────


class TestDraftsCTAGating:
    def test_verified_user_sees_new_post_link(
        self, auth_client, make_user_token, db_session  # noqa: ARG002
    ):
        """Email-verified user should see the active 'New post' anchor."""
        user, _ = make_user_token("verified@test.com", "verified_user", role="reader")
        user.is_email_verified = True
        db.session.commit()

        _login(auth_client, user.id)
        resp = auth_client.get("/drafts/")
        assert resp.status_code == 200
        assert b"/posts/new" in resp.data

    def test_unverified_user_sees_disabled_button(
        self, auth_client, make_user_token, db_session  # noqa: ARG002
    ):
        """Unverified user should see a disabled button instead of the new-post link."""
        user, _ = make_user_token("unverified@test.com", "unverified_u", role="reader")
        user.is_email_verified = False
        db.session.commit()

        _login(auth_client, user.id)
        resp = auth_client.get("/drafts/")
        assert resp.status_code == 200
        # Disabled button present and the new-post href is absent
        assert b"disabled" in resp.data
