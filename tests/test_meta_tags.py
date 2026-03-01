"""Tests for SEO meta-tag output in SSR templates.

Covers:
- base.html default canonical and OG tags
- detail.html full Open Graph article meta
- compare.html compare-specific OG + canonical + noindex
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.user import User, UserRole

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_user(email: str, username: str) -> User:
    u = User(
        email=email,
        username=username,
        password_hash="x",
        role=UserRole.contributor,
    )
    _db.session.add(u)
    _db.session.commit()
    return u


def _make_post(
    author: User,
    *,
    slug: str = "meta-test-post",
    title: str = "Meta Test Post",
    status: PostStatus = PostStatus.published,
    og_image_url: str | None = None,
) -> Post:
    post = Post(
        title=title,
        slug=slug,
        markdown_body="# Hello",
        author_id=author.id,
        status=status,
        published_at=_NOW,
        og_image_url=og_image_url,
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_versioned_post(author: User, *, slug: str) -> Post:
    """Create a published post at version=2 with two PostVersion snapshots."""
    post = Post(
        title="Versioned Post",
        slug=slug,
        markdown_body="# v2 body",
        author_id=author.id,
        status=PostStatus.published,
        published_at=_NOW,
        version=2,
    )
    _db.session.add(post)
    _db.session.commit()

    for vn, body in ((1, "# v1 body"), (2, "# v2 body")):
        _db.session.add(
            PostVersion(post_id=post.id, version_number=vn, markdown_body=body)
        )
    _db.session.commit()
    return post


# ─────────────────────────────────────────────────────────────────────────────
# base.html defaults (any page that doesn't override the blocks)
# ─────────────────────────────────────────────────────────────────────────────


class TestBaseMetaTags:
    """Generic pages render the base.html canonical and OG defaults."""

    def test_canonical_link_present(self, client, db_session):
        """Any SSR page has a <link rel="canonical"> tag."""
        resp = client.get("/")
        body = resp.data.decode()
        assert 'rel="canonical"' in body

    def test_canonical_is_absolute(self, client, db_session):
        """Canonical href starts with the configured PUBLIC_BASE_URL."""
        resp = client.get("/")
        body = resp.data.decode()
        # PUBLIC_BASE_URL is set to "http://testserver" in TestingConfig.
        assert 'href="http://testserver' in body

    def test_default_og_url_present(self, client, db_session):
        resp = client.get("/")
        body = resp.data.decode()
        assert 'property="og:url"' in body

    def test_default_og_title_contains_site_name(self, client, db_session):
        resp = client.get("/")
        body = resp.data.decode()
        assert "og:title" in body
        # Site name should appear in the OG title on the base layout.
        assert "OpenBlog" in body


# ─────────────────────────────────────────────────────────────────────────────
# detail.html — full OG article meta
# ─────────────────────────────────────────────────────────────────────────────


class TestDetailMetaTags:
    """Post detail page renders complete OG article meta."""

    def test_og_type_is_article(self, client, db_session):
        author = _make_user("mt1@x.test", "mt1")
        post = _make_post(author, slug="mt-detail-1")
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert 'content="article"' in body

    def test_og_title_equals_post_title(self, client, db_session):
        author = _make_user("mt2@x.test", "mt2")
        post = _make_post(author, slug="mt-detail-2", title="OG Title Test Post")
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert "OG Title Test Post" in body

    def test_canonical_is_post_url(self, client, db_session):
        author = _make_user("mt3@x.test", "mt3")
        post = _make_post(author, slug="mt-detail-3")
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert f'/posts/{post.slug}"' in body

    def test_og_url_is_absolute(self, client, db_session):
        author = _make_user("mt4@x.test", "mt4")
        post = _make_post(author, slug="mt-detail-4")
        body = client.get(f"/posts/{post.slug}").data.decode()
        # og:url must be an absolute URL.
        assert 'property="og:url" content="http://' in body

    def test_og_site_name_present(self, client, db_session):
        author = _make_user("mt5@x.test", "mt5")
        post = _make_post(author, slug="mt-detail-5")
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert 'property="og:site_name"' in body

    def test_og_image_present_when_set(self, client, db_session):
        author = _make_user("mt6@x.test", "mt6")
        post = _make_post(
            author,
            slug="mt-detail-6",
            og_image_url="/static/img/hero.jpg",
        )
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert 'property="og:image"' in body
        # Must be absolute.
        assert "http://testserver" in body

    def test_og_image_absent_when_not_set(self, client, db_session):
        author = _make_user("mt7@x.test", "mt7")
        post = _make_post(author, slug="mt-detail-7", og_image_url=None)
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert 'property="og:image"' not in body

    def test_twitter_card_present(self, client, db_session):
        author = _make_user("mt8@x.test", "mt8")
        post = _make_post(author, slug="mt-detail-8")
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert 'name="twitter:card"' in body

    def test_no_noindex_on_post_detail(self, client, db_session):
        """Published post detail must NOT have a noindex directive."""
        author = _make_user("mt9@x.test", "mt9")
        post = _make_post(author, slug="mt-detail-9")
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert "noindex" not in body

    def test_published_time_present(self, client, db_session):
        author = _make_user("mt10@x.test", "mt10")
        post = _make_post(author, slug="mt-detail-10")
        body = client.get(f"/posts/{post.slug}").data.decode()
        # published_at is set automatically; the tag should appear.
        assert "article:published_time" in body


# ─────────────────────────────────────────────────────────────────────────────
# compare.html — versioned compare page OG + canonical + noindex
# ─────────────────────────────────────────────────────────────────────────────


class TestCompareMetaTags:
    """Compare page has a versioned canonical, OG meta, and noindex."""

    def _get_compare_page(self, client, post: Post, *, from_v: int = 1, to_v: int = 2):
        return client.get(f"/posts/{post.slug}/compare?from={from_v}&to={to_v}")

    def test_compare_returns_200(self, client, db_session):
        author = _make_user("cmt1@x.test", "cmt1")
        post = _make_versioned_post(author, slug="cmt-post-1")
        resp = self._get_compare_page(client, post)
        assert resp.status_code == 200

    def test_noindex_present(self, client, db_session):
        author = _make_user("cmt2@x.test", "cmt2")
        post = _make_versioned_post(author, slug="cmt-post-2")
        body = self._get_compare_page(client, post).data.decode()
        assert "noindex" in body

    def test_canonical_contains_version_params(self, client, db_session):
        author = _make_user("cmt3@x.test", "cmt3")
        post = _make_versioned_post(author, slug="cmt-post-3")
        body = self._get_compare_page(client, post, from_v=1, to_v=2).data.decode()
        # The canonical link must include the from/to query params.
        assert "from=" in body
        assert "to=" in body

    def test_canonical_is_absolute(self, client, db_session):
        author = _make_user("cmt4@x.test", "cmt4")
        post = _make_versioned_post(author, slug="cmt-post-4")
        body = self._get_compare_page(client, post).data.decode()
        assert 'rel="canonical" href="http://testserver' in body

    def test_og_title_contains_compare(self, client, db_session):
        author = _make_user("cmt5@x.test", "cmt5")
        post = _make_versioned_post(author, slug="cmt-post-5")
        body = self._get_compare_page(client, post).data.decode()
        # og:title must mention "Compare" (or translation equivalent).
        assert "og:title" in body
        assert "Compare" in body or "v1" in body

    def test_og_type_is_website(self, client, db_session):
        author = _make_user("cmt6@x.test", "cmt6")
        post = _make_versioned_post(author, slug="cmt-post-6")
        body = self._get_compare_page(client, post).data.decode()
        assert 'property="og:type" content="website"' in body
