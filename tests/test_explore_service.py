"""Tests for ExploreService."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.models.tag import Tag
from backend.services.explore_service import ExploreService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@example.com", "alice")
    return user


@pytest.fixture()
def tag_python(db_session):
    from backend.extensions import db

    tag = Tag(name="Python", slug="python")
    db.session.add(tag)
    db.session.commit()
    return tag


@pytest.fixture()
def published_posts(alice, tag_python, db_session):
    from backend.extensions import db

    posts = []
    for i in range(3):
        post = Post(
            author_id=alice.id,
            title=f"Published Post {i}",
            slug=f"published-post-{i}",
            markdown_body="# Body",
            status=PostStatus.published,
            tags=[tag_python],
        )
        db.session.add(post)
        posts.append(post)
    db.session.commit()
    return posts


@pytest.fixture()
def draft_post(alice, db_session):
    from backend.extensions import db

    post = Post(
        author_id=alice.id,
        title="Draft Post",
        slug="explore-draft-post",
        markdown_body="# Draft",
        status=PostStatus.draft,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── get_posts ─────────────────────────────────────────────────────────────────


class TestGetPosts:
    def test_returns_only_published(self, published_posts, draft_post, db_session):
        posts, total = ExploreService.get_posts(page=1)
        slugs = [p.slug for p in posts]
        assert all(s.startswith("published-post-") for s in slugs)
        assert "explore-draft-post" not in slugs

    def test_pagination_page_1(self, published_posts, db_session):
        posts, total = ExploreService.get_posts(page=1)
        assert len(posts) <= 20  # _POSTS_PER_PAGE

    def test_empty_when_no_posts(self, db_session):
        posts, total = ExploreService.get_posts(page=1)
        assert total == 0
        assert posts == []


# ── get_topics ────────────────────────────────────────────────────────────────


class TestGetTopics:
    def test_returns_tags_with_posts(self, published_posts, tag_python, db_session):
        topics = ExploreService.get_topics()
        tag_slugs = [t["tag"].slug for t in topics]
        assert "python" in tag_slugs

    def test_returns_empty_when_no_tags(self, db_session):
        topics = ExploreService.get_topics()
        assert topics == []


# ── get_open_revisions ────────────────────────────────────────────────────────


class TestGetOpenRevisions:
    def test_returns_empty_tuple(self, db_session):
        revisions, total = ExploreService.get_open_revisions(page=1)
        assert total == 0
        assert revisions == []


# ── get_accepted_revisions ────────────────────────────────────────────────────


class TestGetAcceptedRevisions:
    def test_returns_empty_tuple(self, db_session):
        revisions, total = ExploreService.get_accepted_revisions(page=1)
        assert total == 0
        assert revisions == []
