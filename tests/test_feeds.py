"""Basic RSS feed content tests.

Tests verifying feed structure, content filtering, and absolute URLs.
HTTP caching (ETag / 304) is covered in test_http_cache.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.portal import ProfileVisibility, UserPrivacySettings
from backend.models.post import Post, PostStatus
from backend.models.tag import Tag
from backend.services.auth_service import AuthService


def _make_user(email: str, username: str):
    return AuthService.register(email, username, "StrongPass123!!")


def _make_published_post(author, slug: str, title: str):
    post = Post(
        slug=slug,
        title=title,
        markdown_body="# Hello",
        status=PostStatus.published,
        author_id=author.id,
        published_at=datetime.now(UTC),
        reading_time_minutes=1,
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_draft_post(author, slug: str = "draft-post"):
    post = Post(
        slug=slug,
        title="Draft Post",
        markdown_body="Draft",
        status=PostStatus.draft,
        author_id=author.id,
        reading_time_minutes=1,
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_tag(name: str, slug: str) -> Tag:
    tag = Tag(name=name, slug=slug)
    _db.session.add(tag)
    _db.session.commit()
    return tag


class TestGlobalFeedContent:
    def test_returns_200(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200

    def test_content_type_rss(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/feed.xml")
        assert "application/rss+xml" in resp.content_type

    def test_valid_rss_version(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/feed.xml")
        assert b'version="2.0"' in resp.data

    def test_published_post_appears(self, auth_client, db_session):
        author = _make_user("feed1@x.test", "feed1")
        _make_published_post(author, "feed-pub-1", "My Published Post")
        resp = auth_client.get("/feed.xml")
        assert b"My Published Post" in resp.data

    def test_draft_excluded(self, auth_client, db_session):
        author = _make_user("feed2@x.test", "feed2")
        _make_published_post(author, "feed-pub-2", "Real Post")
        _make_draft_post(author, "feed-draft-2")
        resp = auth_client.get("/feed.xml")
        assert b"Draft Post" not in resp.data

    def test_post_guid_is_absolute_url(self, auth_client, db_session):
        author = _make_user("feed3@x.test", "feed3")
        _make_published_post(author, "feed-abs-3", "Absolute URL Post")
        resp = auth_client.get("/feed.xml")
        assert b"http://testserver/posts/feed-abs-3" in resp.data

    def test_no_email_in_feed(self, auth_client, db_session):
        author = _make_user("secret@private.test", "feedsecret")
        _make_published_post(author, "feed-secret-1", "Secret Email Post")
        resp = auth_client.get("/feed.xml")
        assert b"secret@private.test" not in resp.data


class TestTagFeedContent:
    def test_404_for_unknown_tag(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/tags/nonexistent-tag/feed.xml")
        assert resp.status_code == 404

    def test_only_tagged_posts_included(self, auth_client, db_session):
        author = _make_user("tf1c@x.test", "tf1c")
        tag = _make_tag("Rust", "rust-tag")
        tagged = _make_published_post(author, "tf-tagged", "Rust Post")
        tagged.tags = [tag]
        _make_published_post(author, "tf-untagged", "Python Post")
        _db.session.commit()

        resp = auth_client.get("/tags/rust-tag/feed.xml")
        assert resp.status_code == 200
        assert b"Rust Post" in resp.data
        assert b"Python Post" not in resp.data


class TestAuthorFeedContent:
    def test_404_for_unknown_user(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/users/ghostuser/feed.xml")
        assert resp.status_code == 404

    def test_private_profile_returns_404(self, auth_client, db_session):
        author = _make_user("priv1@x.test", "privfeed1")
        priv = UserPrivacySettings(
            user_id=author.id,
            profile_visibility=ProfileVisibility.private,
            default_identity_mode="public",
        )
        _db.session.add(priv)
        _db.session.commit()
        _make_published_post(author, "priv-post-1", "Private Author Post")

        resp = auth_client.get("/users/privfeed1/feed.xml")
        assert resp.status_code == 404

    def test_only_author_posts_included(self, auth_client, db_session):
        author1 = _make_user("af1c@x.test", "af1c")
        author2 = _make_user("af2c@x.test", "af2c")
        _make_published_post(author1, "af-post-1c", "Author1 Only")
        _make_published_post(author2, "af-post-2c", "Author2 Only")

        resp = auth_client.get("/users/af1c/feed.xml")
        assert b"Author1 Only" in resp.data
        assert b"Author2 Only" not in resp.data
