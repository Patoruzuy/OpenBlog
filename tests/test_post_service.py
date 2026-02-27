"""Unit tests for PostService and the markdown/slug utilities.

All tests use the ``db_session`` fixture which creates SQLite in-memory tables
and injects a _FakeRedis stub, so no external services are required.
"""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.models.user import User
from backend.services.auth_service import AuthService
from backend.services.post_service import PostError, PostService, _slugify, _unique_slug
from backend.utils.markdown import reading_time_minutes, render_markdown

# ── Helpers ────────────────────────────────────────────────────────────────────


def make_author(db_session) -> User:
    user = AuthService.register("author@example.com", "postauthor", "StrongPass123!!")
    return db_session.merge(user)


# ── Slug helpers ───────────────────────────────────────────────────────────────


class TestSlugify:
    def test_lowercases(self):
        assert _slugify("Hello World") == "hello-world"

    def test_strips_punctuation(self):
        assert _slugify("Flask 3.0: What's New?") == "flask-30-whats-new"

    def test_collapses_spaces(self):
        assert _slugify("a   b") == "a-b"

    def test_underscore_becomes_hyphen(self):
        assert _slugify("my_post_title") == "my-post-title"

    def test_leading_trailing_hyphens_stripped(self):
        assert _slugify("---hello---") == "hello"

    def test_empty_returns_untitled(self):
        assert _slugify("") == "untitled"

    def test_numbers_preserved(self):
        assert _slugify("Top 10 Tips") == "top-10-tips"


class TestUniqueSlug:
    def test_returns_base_when_no_collision(self, db_session):  # noqa: ARG002
        slug = _unique_slug("my-new-post")
        assert slug == "my-new-post"

    def test_appends_counter_on_collision(self, db_session):
        author = make_author(db_session)
        PostService.create(author.id, "My Post")
        slug = _unique_slug("my-post")
        assert slug == "my-post-2"

    def test_increments_counter_further(self, db_session):
        author = make_author(db_session)
        PostService.create(author.id, "Dup Post")
        PostService.create(author.id, "Dup Post")  # will get dup-post-2
        slug = _unique_slug("dup-post")
        assert slug == "dup-post-3"


# ── Reading time ───────────────────────────────────────────────────────────────


class TestReadingTime:
    def test_empty_returns_1(self):
        assert reading_time_minutes("") == 1

    def test_short_text_returns_1(self):
        assert reading_time_minutes("Hello world") == 1

    def test_400_words_returns_2(self):
        text = ("word " * 400).strip()
        assert reading_time_minutes(text) == 2

    def test_200_words_returns_1(self):
        text = ("word " * 200).strip()
        assert reading_time_minutes(text) == 1

    def test_201_words_returns_2(self):
        text = ("word " * 201).strip()
        assert reading_time_minutes(text) == 2


# ── render_markdown ────────────────────────────────────────────────────────────


class TestRenderMarkdown:
    def test_paragraph(self):
        html = render_markdown("Hello **world**.")
        assert "<strong>world</strong>" in html

    def test_heading(self):
        html = render_markdown("# Title")
        assert "<h1>" in html

    def test_fenced_code(self):
        md = "```python\nprint('hi')\n```"
        html = render_markdown(md)
        assert "<code" in html

    def test_xss_stripped(self):
        html = render_markdown("<script>alert(1)</script>")
        assert "<script>" not in html

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = render_markdown(md)
        assert "<table>" in html


# ── PostService.create ─────────────────────────────────────────────────────────


class TestPostServiceCreate:
    def test_creates_draft_by_default(self, db_session):
        author = make_author(db_session)
        post = PostService.create(author.id, "My First Post")
        assert post.id is not None
        assert post.status == PostStatus.draft
        assert post.slug == "my-first-post"

    def test_slug_derived_from_title(self, db_session):
        author = make_author(db_session)
        post = PostService.create(author.id, "Flask & SQLAlchemy Tips!")
        assert post.slug == "flask-sqlalchemy-tips"

    def test_slug_made_unique_on_collision(self, db_session):
        author = make_author(db_session)
        p1 = PostService.create(author.id, "Same Title")
        p2 = PostService.create(author.id, "Same Title")
        assert p1.slug == "same-title"
        assert p2.slug == "same-title-2"

    def test_reading_time_set(self, db_session):
        author = make_author(db_session)
        body = "word " * 250
        post = PostService.create(author.id, "Long Post", body)
        assert post.reading_time_minutes == 2

    def test_tags_attached(self, db_session):
        author = make_author(db_session)
        post = PostService.create(author.id, "Tagged Post", tags=["Flask", "Python"])
        tag_slugs = {t.slug for t in post.tags}
        assert "flask" in tag_slugs
        assert "python" in tag_slugs

    def test_empty_title_raises(self, db_session):
        author = make_author(db_session)
        with pytest.raises(PostError) as exc_info:
            PostService.create(author.id, "   ")
        assert exc_info.value.status_code == 400


# ── PostService.update ─────────────────────────────────────────────────────────


class TestPostServiceUpdate:
    def _make_post(self, db_session) -> Post:
        author = make_author(db_session)
        return PostService.create(author.id, "Original Title", "Original body.")

    def test_updates_title(self, db_session):
        post = self._make_post(db_session)
        updated = PostService.update(post, title="New Title")
        assert updated.title == "New Title"

    def test_content_change_bumps_version(self, db_session):
        post = self._make_post(db_session)
        assert post.version == 1
        PostService.update(post, markdown_body="Completely new body.")
        assert post.version == 2

    def test_same_content_no_version_bump(self, db_session):
        post = self._make_post(db_session)
        PostService.update(post, markdown_body="Original body.")
        assert post.version == 1

    def test_no_fields_no_version_bump(self, db_session):
        post = self._make_post(db_session)
        PostService.update(post)
        assert post.version == 1

    def test_update_tags(self, db_session):
        post = self._make_post(db_session)
        PostService.update(post, tags=["redis"])
        assert any(t.slug == "redis" for t in post.tags)

    def test_empty_title_raises(self, db_session):
        post = self._make_post(db_session)
        with pytest.raises(PostError):
            PostService.update(post, title="")

    def test_content_change_invalidates_cache(self, app, db_session):
        post = self._make_post(db_session)
        # Prime the cache.
        with app.app_context():
            from backend.utils.markdown import get_rendered_html
            get_rendered_html(post.id, post.markdown_body)
            redis = app.extensions["redis"]
            assert redis.get(f"post:{post.id}:html") is not None
            # Updating content should delete the cached key.
            PostService.update(post, markdown_body="Brand new content.")
            assert redis.get(f"post:{post.id}:html") is None


# ── PostService.publish ────────────────────────────────────────────────────────


class TestPostServicePublish:
    def _draft(self, db_session) -> Post:
        author = make_author(db_session)
        return PostService.create(author.id, "Draft Post")

    def test_publish_immediately(self, db_session):
        post = self._draft(db_session)
        PostService.publish(post)
        assert post.status == PostStatus.published
        assert post.published_at is not None

    def test_schedule_future(self, db_session):  # noqa: ARG002
        from datetime import UTC, datetime, timedelta
        post = self._draft(db_session)
        future = datetime.now(UTC) + timedelta(days=1)
        PostService.publish(post, at=future)
        assert post.status == PostStatus.scheduled
        # SQLite drops tzinfo on round-trip; compare naive UTC values.
        assert post.publish_at.replace(tzinfo=None) == future.replace(tzinfo=None)

    def test_schedule_past_raises(self, db_session):
        from datetime import UTC, datetime, timedelta
        post = self._draft(db_session)
        past = datetime.now(UTC) - timedelta(hours=1)
        with pytest.raises(PostError) as exc_info:
            PostService.publish(post, at=past)
        assert exc_info.value.status_code == 400

    def test_archive(self, db_session):
        post = self._draft(db_session)
        PostService.archive(post)
        assert post.status == PostStatus.archived


# ── PostService listing ────────────────────────────────────────────────────────


class TestPostServiceList:
    def _author_and_published(self, db_session, n: int = 3) -> list[Post]:
        author = make_author(db_session)
        posts = []
        for i in range(n):
            p = PostService.create(author.id, f"Post {i}")
            PostService.publish(p)
            posts.append(p)
        return posts

    def test_only_published_returned(self, db_session):
        author = make_author(db_session)
        pub = PostService.create(author.id, "Published")
        PostService.publish(pub)
        PostService.create(author.id, "Draft")  # remains draft
        posts, total = PostService.list_published()
        assert total == 1
        assert posts[0].slug == "published"

    def test_pagination(self, db_session):
        self._author_and_published(db_session, n=5)
        page1, total = PostService.list_published(page=1, per_page=3)
        assert total == 5
        assert len(page1) == 3
        page2, _ = PostService.list_published(page=2, per_page=3)
        assert len(page2) == 2

    def test_filter_by_tag(self, db_session):
        author = make_author(db_session)
        tagged = PostService.create(author.id, "Tagged", tags=["flask"])
        PostService.publish(tagged)
        untagged = PostService.create(author.id, "Untagged")
        PostService.publish(untagged)

        posts, total = PostService.list_published(tag_slug="flask")
        assert total == 1
        assert posts[0].slug == "tagged"

    def test_list_all_includes_drafts(self, db_session):
        author = make_author(db_session)
        PostService.create(author.id, "Draft One")
        pub = PostService.create(author.id, "Published One")
        PostService.publish(pub)
        _, total = PostService.list_all()
        assert total == 2
