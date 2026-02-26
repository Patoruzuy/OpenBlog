"""Unit tests for the SSR tags index page (GET /tags/)."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.models.tag import Tag
from backend.models.user import User

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def _tags_with_posts(db_session):
    """Two tags; first has one published post, second has none."""
    from backend.extensions import db

    tag_py = Tag(name="Python", slug="python", description="Python programming")
    tag_go = Tag(name="Go", slug="go", description="Go language")
    db.session.add_all([tag_py, tag_go])
    db.session.flush()

    author = User(
        email="tagauthor@example.com",
        username="tagauthor",
        password_hash="x",
        is_active=True,
    )
    db.session.add(author)
    db.session.flush()

    post = Post(
        title="Python Post",
        slug="python-post",
        markdown_body="# Hello Python",
        author_id=author.id,
        status=PostStatus.published,
    )
    post.tags.append(tag_py)
    db.session.add(post)
    db.session.commit()
    return tag_py, tag_go


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestTagsPage:
    def test_returns_200(self, auth_client, db_session):
        resp = auth_client.get("/tags/")
        assert resp.status_code == 200

    def test_content_type_is_html(self, auth_client, db_session):
        resp = auth_client.get("/tags/")
        assert "text/html" in resp.content_type

    def test_shows_tag_slug(self, auth_client, _tags_with_posts):
        resp = auth_client.get("/tags/")
        assert b"python" in resp.data

    def test_shows_both_tags(self, auth_client, _tags_with_posts):
        resp = auth_client.get("/tags/")
        assert b"python" in resp.data
        assert b"go" in resp.data

    def test_shows_post_count_for_tagged_post(self, auth_client, _tags_with_posts):
        resp = auth_client.get("/tags/")
        assert b"1 post" in resp.data

    def test_tag_with_no_posts_shows_zero(self, auth_client, _tags_with_posts):
        resp = auth_client.get("/tags/")
        # Go tag has 0 posts; "0 posts" should appear
        assert b"0 posts" in resp.data

    def test_draft_posts_not_counted(self, auth_client, db_session):
        """A tag whose only post is a draft must show 0 posts."""
        from backend.extensions import db

        tag = Tag(name="Rust", slug="rust")
        db.session.add(tag)
        db.session.flush()

        author = User(
            email="rustdev@example.com",
            username="rustdev",
            password_hash="x",
            is_active=True,
        )
        db.session.add(author)
        db.session.flush()

        draft = Post(
            title="Draft Post",
            slug="draft-rust",
            markdown_body="# Draft",
            author_id=author.id,
            status=PostStatus.draft,
        )
        draft.tags.append(tag)
        db.session.add(draft)
        db.session.commit()

        resp = auth_client.get("/tags/")
        assert resp.status_code == 200
        assert b"rust" in resp.data
        # The tag exists but 0 published posts
        assert b"0 posts" in resp.data

    def test_empty_state_when_no_tags(self, auth_client, db_session):
        resp = auth_client.get("/tags/")
        assert resp.status_code == 200
        assert b"No topics yet" in resp.data

    def test_tag_link_points_to_filtered_posts(self, auth_client, _tags_with_posts):
        resp = auth_client.get("/tags/")
        assert b"tag=python" in resp.data

    def test_description_shown(self, auth_client, _tags_with_posts):
        resp = auth_client.get("/tags/")
        assert b"Python programming" in resp.data
