"""Unit tests for CommentService.

Uses the ``db_session`` fixture which creates SQLite in-memory tables and
injects a _FakeRedis stub, so no external services are required.
"""

from __future__ import annotations

import pytest

from backend.models.post import Post
from backend.services.auth_service import AuthService
from backend.services.comment_service import CommentError, CommentService
from backend.services.post_service import PostService

# ── Helpers ────────────────────────────────────────────────────────────────────


def make_user(email: str, username: str):
    return AuthService.register(email, username, "StrongPass123!!")


def make_published_post(author_id: int, title: str = "Test Post") -> Post:
    post = PostService.create(author_id, title)
    return PostService.publish(post)


# ── Create ────────────────────────────────────────────────────────────────────


class TestCommentServiceCreate:
    def test_create_top_level(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Hello world")
        assert comment.id is not None
        assert comment.parent_id is None
        assert comment.body == "Hello world"
        assert comment.is_deleted is False
        assert comment.is_flagged is False

    def test_create_reply(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        parent = CommentService.create(post.id, user.id, "Parent comment")
        reply = CommentService.create(post.id, user.id, "Reply", parent_id=parent.id)
        assert reply.parent_id == parent.id

    def test_create_empty_body_raises(self, db_session):  # noqa: ARG002
        with pytest.raises(CommentError, match="empty"):
            CommentService.create(1, 1, "   ")

    def test_create_deep_nesting_raises(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        parent = CommentService.create(post.id, user.id, "Top")
        reply = CommentService.create(post.id, user.id, "Reply", parent_id=parent.id)
        with pytest.raises(CommentError, match="one level"):
            CommentService.create(post.id, user.id, "Deep", parent_id=reply.id)

    def test_create_reply_wrong_post_raises(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post1 = make_published_post(user.id, "Post One")
        post2 = make_published_post(user.id, "Post Two")
        parent = CommentService.create(post1.id, user.id, "Parent on post1")
        with pytest.raises(CommentError):
            CommentService.create(post2.id, user.id, "Bad reply", parent_id=parent.id)


# ── Update ────────────────────────────────────────────────────────────────────


class TestCommentServiceUpdate:
    def test_update_body(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Original")
        updated = CommentService.update(comment, "Updated body", editor_id=user.id)
        assert updated.body == "Updated body"

    def test_update_by_non_author_raises(self, db_session):  # noqa: ARG002
        author = make_user("a@test.com", "user_a")
        other = make_user("b@test.com", "user_b")
        post = make_published_post(author.id)
        comment = CommentService.create(post.id, author.id, "Hello")
        with pytest.raises(CommentError, match="author"):
            CommentService.update(comment, "Changed", editor_id=other.id)

    def test_update_deleted_raises(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Hello")
        CommentService.delete(comment, user_id=user.id, user_role="contributor")
        with pytest.raises(CommentError, match="deleted"):
            CommentService.update(comment, "New body", editor_id=user.id)

    def test_update_empty_body_raises(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Hello")
        with pytest.raises(CommentError, match="empty"):
            CommentService.update(comment, "  ", editor_id=user.id)


# ── Delete ────────────────────────────────────────────────────────────────────


class TestCommentServiceDelete:
    def test_soft_delete_by_author(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Hello")
        CommentService.delete(comment, user_id=user.id, user_role="contributor")
        assert comment.is_deleted is True
        assert comment.body == "[deleted]"

    def test_delete_by_editor(self, db_session):  # noqa: ARG002
        author = make_user("a@test.com", "user_a")
        editor = make_user("e@test.com", "editor_e")
        post = make_published_post(author.id)
        comment = CommentService.create(post.id, author.id, "Hello")
        CommentService.delete(comment, user_id=editor.id, user_role="editor")
        assert comment.is_deleted is True

    def test_delete_by_non_author_reader_raises(self, db_session):  # noqa: ARG002
        author = make_user("a@test.com", "user_a")
        other = make_user("b@test.com", "user_b")
        post = make_published_post(author.id)
        comment = CommentService.create(post.id, author.id, "Hello")
        with pytest.raises(CommentError, match="authorised"):
            CommentService.delete(comment, user_id=other.id, user_role="reader")


# ── Flag / unflag ─────────────────────────────────────────────────────────────


class TestCommentServiceFlag:
    def test_flag_comment(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Hello")
        CommentService.flag(comment)
        assert comment.is_flagged is True

    def test_unflag_by_admin(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Hello")
        CommentService.flag(comment)
        CommentService.unflag(comment, user_role="admin")
        assert comment.is_flagged is False

    def test_unflag_by_non_mod_raises(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Hello")
        CommentService.flag(comment)
        with pytest.raises(CommentError, match="admin"):
            CommentService.unflag(comment, user_role="reader")


# ── List ──────────────────────────────────────────────────────────────────────


class TestCommentServiceList:
    def test_list_top_level_with_replies(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        c1 = CommentService.create(post.id, user.id, "Top level")
        CommentService.create(post.id, user.id, "Reply", parent_id=c1.id)
        comments = CommentService.list_for_post(post.id)
        assert len(comments) == 1  # only top-level returned
        assert len(comments[0].replies) == 1

    def test_list_excludes_flagged_by_default(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Spam")
        CommentService.flag(comment)
        assert CommentService.list_for_post(post.id) == []

    def test_list_includes_flagged_for_mod(self, db_session):  # noqa: ARG002
        user = make_user("a@test.com", "user_a")
        post = make_published_post(user.id)
        comment = CommentService.create(post.id, user.id, "Spam")
        CommentService.flag(comment)
        results = CommentService.list_for_post(post.id, include_flagged=True)
        assert len(results) == 1
        assert results[0].id == comment.id
