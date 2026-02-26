"""Tests for BookmarkService."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.services.bookmark_service import BookmarkError, BookmarkService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@example.com", "alice")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _ = make_user_token("bob@example.com", "bob")
    return user


@pytest.fixture()
def pub_post(alice, db_session):
    from backend.extensions import db

    post = Post(
        author_id=alice.id,
        title="Bookmarkable Post",
        slug="bookmarkable-post",
        markdown_body="# Hello",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


@pytest.fixture()
def draft_post(alice, db_session):
    from backend.extensions import db

    post = Post(
        author_id=alice.id,
        title="Draft Post",
        slug="draft-bookmark-post",
        markdown_body="# Draft",
        status=PostStatus.draft,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── add ───────────────────────────────────────────────────────────────────────


class TestAdd:
    def test_add_returns_bookmark(self, bob, pub_post, db_session):
        bm = BookmarkService.add(bob.id, pub_post.id)
        assert bm.user_id == bob.id
        assert bm.post_id == pub_post.id

    def test_add_sets_has_bookmarked(self, bob, pub_post, db_session):
        BookmarkService.add(bob.id, pub_post.id)
        assert BookmarkService.has_bookmarked(bob.id, pub_post.id) is True

    def test_cannot_bookmark_draft(self, bob, draft_post, db_session):
        with pytest.raises(BookmarkError) as exc_info:
            BookmarkService.add(bob.id, draft_post.id)
        assert exc_info.value.status_code == 404

    def test_cannot_bookmark_nonexistent(self, bob, db_session):
        with pytest.raises(BookmarkError) as exc_info:
            BookmarkService.add(bob.id, 99999)
        assert exc_info.value.status_code == 404

    def test_duplicate_raises_409(self, bob, pub_post, db_session):
        BookmarkService.add(bob.id, pub_post.id)
        with pytest.raises(BookmarkError) as exc_info:
            BookmarkService.add(bob.id, pub_post.id)
        assert exc_info.value.status_code == 409


# ── remove ────────────────────────────────────────────────────────────────────


class TestRemove:
    def test_remove_clears_has_bookmarked(self, bob, pub_post, db_session):
        BookmarkService.add(bob.id, pub_post.id)
        BookmarkService.remove(bob.id, pub_post.id)
        assert BookmarkService.has_bookmarked(bob.id, pub_post.id) is False

    def test_remove_not_bookmarked_raises_404(self, bob, pub_post, db_session):
        with pytest.raises(BookmarkError) as exc_info:
            BookmarkService.remove(bob.id, pub_post.id)
        assert exc_info.value.status_code == 404


# ── has_bookmarked ────────────────────────────────────────────────────────────


class TestHasBookmarked:
    def test_false_by_default(self, bob, pub_post, db_session):
        assert BookmarkService.has_bookmarked(bob.id, pub_post.id) is False


# ── list_for_user ─────────────────────────────────────────────────────────────


class TestListForUser:
    def test_empty_list(self, bob, db_session):
        posts, total = BookmarkService.list_for_user(bob.id)
        assert total == 0
        assert posts == []

    def test_returns_bookmarked_posts(self, alice, bob, pub_post, db_session):
        BookmarkService.add(bob.id, pub_post.id)
        posts, total = BookmarkService.list_for_user(bob.id)
        assert total == 1
        assert posts[0].id == pub_post.id

    def test_pagination(self, alice, bob, db_session):
        from backend.extensions import db

        slugs = []
        for i in range(3):
            post = Post(
                author_id=alice.id,
                title=f"Post {i}",
                slug=f"bm-page-post-{i}",
                markdown_body="# Hi",
                status=PostStatus.published,
            )
            db.session.add(post)
            db.session.flush()
            slugs.append(post.id)

        db.session.commit()
        for pid in slugs:
            BookmarkService.add(bob.id, pid)

        posts, total = BookmarkService.list_for_user(bob.id, page=1, per_page=2)
        assert total == 3
        assert len(posts) == 2

    def test_only_own_bookmarks_returned(self, alice, bob, pub_post, db_session):
        BookmarkService.add(alice.id, pub_post.id)
        posts, total = BookmarkService.list_for_user(bob.id)
        assert total == 0
