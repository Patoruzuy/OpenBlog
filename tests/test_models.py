"""Model unit tests using SQLite in-memory (no Docker required).

Each test class gets a fresh schema via the ``db_session`` fixture, which
calls ``db.create_all()`` / ``db.drop_all()`` around every test function.

Tests focus on:
  - record creation and column defaults
  - unique-constraint enforcement
  - self-referential relationships (threaded comments)
  - vote duplicate prevention
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.comment import Comment
from backend.models.post import Post, PostStatus
from backend.models.tag import Tag
from backend.models.user import User, UserRole
from backend.models.vote import Vote

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_user(db_session, email="u@example.com", username="user") -> User:
    user = User(email=email, username=username, password_hash="hash")
    db_session.add(user)
    db_session.flush()
    return user


def make_post(db_session, author: User, slug="test-post") -> Post:
    post = Post(slug=slug, title="Test Post", markdown_body="# Hi", author_id=author.id)
    db_session.add(post)
    db_session.flush()
    return post


# ── User ──────────────────────────────────────────────────────────────────────


class TestUser:
    def test_create_defaults(self, db_session):
        user = make_user(db_session)
        db_session.commit()
        assert user.id is not None
        assert user.role == UserRole.reader
        assert user.reputation_score == 0
        assert user.is_active is True
        assert user.is_email_verified is False
        assert user.is_shadow_banned is False

    def test_repr_contains_username(self, db_session):
        user = make_user(db_session, username="alice")
        db_session.commit()
        assert "alice" in repr(user)

    def test_unique_email_constraint(self, db_session):
        make_user(db_session, email="dup@example.com", username="a")
        db_session.commit()
        db_session.add(User(email="dup@example.com", username="b", password_hash="h"))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_unique_username_constraint(self, db_session):
        make_user(db_session, email="a@example.com", username="same")
        db_session.commit()
        db_session.add(User(email="b@example.com", username="same", password_hash="h"))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_admin_role_assignment(self, db_session):
        user = User(
            email="admin@example.com",
            username="admin",
            password_hash="h",
            role=UserRole.admin,
        )
        db_session.add(user)
        db_session.commit()
        assert user.role == UserRole.admin


# ── Post ──────────────────────────────────────────────────────────────────────


class TestPost:
    def test_create_defaults(self, db_session):
        author = make_user(db_session)
        post = make_post(db_session, author)
        db_session.commit()
        assert post.id is not None
        assert post.status == PostStatus.draft
        assert post.version == 1
        assert post.view_count == 0

    def test_slug_unique_constraint(self, db_session):
        author = make_user(db_session)
        make_post(db_session, author, slug="same-slug")
        db_session.commit()
        db_session.add(Post(slug="same-slug", title="B", markdown_body="", author_id=author.id))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_post_with_tags(self, db_session):
        author = make_user(db_session)
        tag = Tag(name="Python", slug="python")
        db_session.add(tag)
        post = make_post(db_session, author)
        post.tags = [tag]
        db_session.commit()

        fetched = db_session.get(Post, post.id)
        tag_list = list(fetched.tags)
        assert len(tag_list) == 1
        assert tag_list[0].slug == "python"

    def test_published_status(self, db_session):
        author = make_user(db_session)
        post = Post(
            slug="pub",
            title="Published",
            markdown_body="",
            author_id=author.id,
            status=PostStatus.published,
        )
        db_session.add(post)
        db_session.commit()
        assert post.status == PostStatus.published


# ── Tag ───────────────────────────────────────────────────────────────────────


class TestTag:
    def test_create_tag(self, db_session):
        tag = Tag(name="Flask", slug="flask")
        db_session.add(tag)
        db_session.commit()
        assert tag.id is not None
        assert tag.name == "Flask"

    def test_slug_unique_constraint(self, db_session):
        db_session.add(Tag(name="Tag One", slug="dup"))
        db_session.commit()
        db_session.add(Tag(name="Tag Two", slug="dup"))
        with pytest.raises(IntegrityError):
            db_session.commit()


# ── Comment ───────────────────────────────────────────────────────────────────


class TestComment:
    def test_create_comment(self, db_session):
        author = make_user(db_session)
        post = make_post(db_session, author)
        comment = Comment(post_id=post.id, author_id=author.id, body="Great post!")
        db_session.add(comment)
        db_session.commit()
        assert comment.id is not None
        assert comment.is_deleted is False
        assert comment.is_flagged is False

    def test_threaded_reply(self, db_session):
        author = make_user(db_session)
        post = make_post(db_session, author)
        parent = Comment(post_id=post.id, author_id=author.id, body="Parent comment")
        db_session.add(parent)
        db_session.flush()
        child = Comment(
            post_id=post.id, author_id=author.id, body="Reply", parent_id=parent.id
        )
        db_session.add(child)
        db_session.commit()
        assert child.parent_id == parent.id


# ── Vote ──────────────────────────────────────────────────────────────────────


class TestVote:
    def test_create_vote(self, db_session):
        user = make_user(db_session)
        vote = Vote(user_id=user.id, target_type="post", target_id=1)
        db_session.add(vote)
        db_session.commit()
        assert vote.id is not None

    def test_unique_vote_constraint(self, db_session):
        user = make_user(db_session)
        db_session.add(Vote(user_id=user.id, target_type="post", target_id=42))
        db_session.commit()
        db_session.add(Vote(user_id=user.id, target_type="post", target_id=42))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_different_target_types_allowed(self, db_session):
        """The same (user, target_id) is allowed with different target_types."""
        user = make_user(db_session)
        db_session.add(Vote(user_id=user.id, target_type="post", target_id=1))
        db_session.add(Vote(user_id=user.id, target_type="comment", target_id=1))
        db_session.commit()
        count = db_session.query(Vote).filter_by(user_id=user.id).count()
        assert count == 2
