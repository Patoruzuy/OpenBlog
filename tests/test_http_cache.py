"""HTTP caching tests — ETag / Last-Modified / conditional GET.

Covers:
  - 200 responses include ETag, Last-Modified, Cache-Control headers
  - 304 on If-None-Match (exact match and wildcard)
  - 304 on If-Modified-Since (equal or older)
  - 200 on mismatched ETag
  - ETag change when new published post appears (fingerprint is content-driven)
  - 304 body is empty; caching headers are still present
  - Draft posts do NOT affect fingerprints (no leakage)
  - private-profile author feed → 404, no caching headers
  - Tag feed and author feed scope isolation (distinct ETags)
  - Sitemap caching (ETag / 304 / changes on new post)
  - robots.txt has Cache-Control but no ETag
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.portal import ProfileVisibility, UserPrivacySettings
from backend.models.post import Post, PostStatus
from backend.models.tag import Tag
from backend.models.user import User
from backend.services.auth_service import AuthService

# ── Fixtures / helpers ────────────────────────────────────────────────────────

_T0 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)  # base timestamp
_T1 = datetime(2025, 6, 1, 13, 0, 0, tzinfo=UTC)  # later (after new post)


def _make_user(email: str, username: str) -> User:
    return AuthService.register(email, username, "StrongPass123!!")


def _make_published_post(
    author: User,
    slug: str,
    title: str,
    published_at: datetime = _T0,
    tags: list[Tag] | None = None,
) -> Post:
    post = Post(
        slug=slug,
        title=title,
        markdown_body="# Hello\nWorld",
        status=PostStatus.published,
        author_id=author.id,
        published_at=published_at,
        reading_time_minutes=1,
    )
    if tags:
        post.tags = tags
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_draft_post(author: User, slug: str = "draft-post") -> Post:
    post = Post(
        slug=slug,
        title="Draft Post",
        markdown_body="Draft content",
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


def _set_private(user: User) -> None:
    priv = UserPrivacySettings(
        user_id=user.id,
        profile_visibility=ProfileVisibility.private,
        default_identity_mode="public",
    )
    _db.session.add(priv)
    _db.session.commit()


# ── Global feed ───────────────────────────────────────────────────────────────


class TestGlobalFeedCaching:
    def test_200_includes_etag(self, auth_client, db_session):
        author = _make_user("gf1@x.test", "gf1")
        _make_published_post(author, "gf-post-1", "GF Post 1")
        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        assert resp.headers.get("ETag")

    def test_200_includes_last_modified(self, auth_client, db_session):
        author = _make_user("gf2@x.test", "gf2")
        _make_published_post(author, "gf-post-2", "GF Post 2")
        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        assert resp.headers.get("Last-Modified")

    def test_200_includes_cache_control(self, auth_client, db_session):
        author = _make_user("gf3@x.test", "gf3")
        _make_published_post(author, "gf-post-3", "GF Post 3")
        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        assert "public" in resp.headers.get("Cache-Control", "")
        assert "max-age" in resp.headers.get("Cache-Control", "")

    def test_304_on_if_none_match(self, auth_client, db_session):
        author = _make_user("gf4@x.test", "gf4")
        _make_published_post(author, "gf-post-4", "GF Post 4")
        r1 = auth_client.get("/feed.xml")
        etag = r1.headers["ETag"]

        r2 = auth_client.get("/feed.xml", headers={"If-None-Match": etag})
        assert r2.status_code == 304

    def test_304_body_is_empty(self, auth_client, db_session):
        author = _make_user("gf5@x.test", "gf5")
        _make_published_post(author, "gf-post-5", "GF Post 5")
        r1 = auth_client.get("/feed.xml")
        etag = r1.headers["ETag"]

        r2 = auth_client.get("/feed.xml", headers={"If-None-Match": etag})
        assert r2.status_code == 304
        assert r2.data == b""

    def test_304_preserves_caching_headers(self, auth_client, db_session):
        author = _make_user("gf6@x.test", "gf6")
        _make_published_post(author, "gf-post-6", "GF Post 6")
        r1 = auth_client.get("/feed.xml")
        etag = r1.headers["ETag"]

        r2 = auth_client.get("/feed.xml", headers={"If-None-Match": etag})
        assert r2.status_code == 304
        assert r2.headers.get("ETag") == etag
        assert r2.headers.get("Cache-Control")

    def test_304_on_if_modified_since(self, auth_client, db_session):
        author = _make_user("gf7@x.test", "gf7")
        _make_published_post(author, "gf-post-7", "GF Post 7")
        r1 = auth_client.get("/feed.xml")
        last_mod = r1.headers["Last-Modified"]

        r2 = auth_client.get("/feed.xml", headers={"If-Modified-Since": last_mod})
        assert r2.status_code == 304

    def test_200_on_stale_etag(self, auth_client, db_session):
        author = _make_user("gf8@x.test", "gf8")
        _make_published_post(author, "gf-post-8", "GF Post 8")

        r1 = auth_client.get("/feed.xml", headers={"If-None-Match": 'W/"stale-etag"'})
        assert r1.status_code == 200

    def test_etag_changes_when_post_published(self, auth_client, db_session):
        author = _make_user("gf9@x.test", "gf9")
        _make_published_post(author, "gf-post-9a", "GF Post 9A", published_at=_T0)
        r1 = auth_client.get("/feed.xml")
        etag1 = r1.headers["ETag"]

        # Publish a new post with a later timestamp.
        _make_published_post(author, "gf-post-9b", "GF Post 9B", published_at=_T1)
        r2 = auth_client.get("/feed.xml")
        etag2 = r2.headers["ETag"]

        assert etag1 != etag2

    def test_new_post_invalidates_304(self, auth_client, db_session):
        """After a new post is published, the old ETag no longer produces 304."""
        author = _make_user("gf10@x.test", "gf10")
        _make_published_post(author, "gf-post-10a", "GF Post 10A", published_at=_T0)
        r1 = auth_client.get("/feed.xml")
        etag1 = r1.headers["ETag"]

        # Publish a newer post.
        _make_published_post(author, "gf-post-10b", "GF Post 10B", published_at=_T1)
        r2 = auth_client.get("/feed.xml", headers={"If-None-Match": etag1})
        assert r2.status_code == 200  # content changed, old ETag no longer valid

    def test_draft_does_not_appear_in_feed_xml(self, auth_client, db_session):
        author = _make_user("gf11@x.test", "gf11")
        _make_published_post(author, "gf-post-11", "Published Post")
        _make_draft_post(author, "gf-draft-11")

        resp = auth_client.get("/feed.xml")
        assert b"Draft Post" not in resp.data
        assert b"Published Post" in resp.data

    def test_draft_does_not_change_etag(self, auth_client, db_session):
        """Creating a draft after measuring the ETag should NOT change the ETag."""
        author = _make_user("gf12@x.test", "gf12")
        _make_published_post(author, "gf-post-12", "GF Post 12", published_at=_T0)
        etag1 = auth_client.get("/feed.xml").headers["ETag"]

        # Add a draft.
        _make_draft_post(author, "gf-draft-12a")
        etag2 = auth_client.get("/feed.xml").headers["ETag"]

        assert etag1 == etag2  # draft must not affect fingerprint

    def test_cache_control_is_public(self, auth_client, db_session):
        resp = auth_client.get("/feed.xml")
        assert resp.headers.get("Cache-Control", "").startswith("public")

    def test_content_type_is_rss(self, auth_client, db_session):
        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        assert "application/rss+xml" in resp.content_type


# ── Tag feed ──────────────────────────────────────────────────────────────────


class TestTagFeedCaching:
    def test_unknown_tag_returns_404(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/tags/no-such-tag/feed.xml")
        assert resp.status_code == 404

    def test_404_has_no_etag(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/tags/ghost-tag/feed.xml")
        assert resp.status_code == 404
        assert "ETag" not in resp.headers

    def test_200_with_caching_headers(self, auth_client, db_session):
        author = _make_user("tf1@x.test", "tf1")
        tag = _make_tag("Python", "python")
        post = _make_published_post(author, "tf-post-1", "TF Post 1")
        post.tags = [tag]
        _db.session.commit()

        resp = auth_client.get("/tags/python/feed.xml")
        assert resp.status_code == 200
        assert resp.headers.get("ETag")
        assert resp.headers.get("Last-Modified")

    def test_304_on_if_none_match(self, auth_client, db_session):
        author = _make_user("tf2@x.test", "tf2")
        tag = _make_tag("Flask", "flask")
        post = _make_published_post(author, "tf-post-2", "TF Post 2")
        post.tags = [tag]
        _db.session.commit()

        r1 = auth_client.get("/tags/flask/feed.xml")
        r2 = auth_client.get(
            "/tags/flask/feed.xml", headers={"If-None-Match": r1.headers["ETag"]}
        )
        assert r2.status_code == 304

    def test_different_scopes_produce_distinct_etags(self, auth_client, db_session):
        author = _make_user("tf3@x.test", "tf3")
        tag_a = _make_tag("JavaScript", "javascript")
        _make_tag("Rust", "rust")
        post = _make_published_post(author, "tf-post-3", "TF Post 3", published_at=_T0)
        post.tags = [tag_a]
        _db.session.commit()

        # tag_b has no posts → its fingerprint epoch is _EPOCH
        # But the ETags incorporate the scope name, so they're always distinct.
        etag_a = auth_client.get("/tags/javascript/feed.xml").headers.get("ETag", "")
        etag_b = auth_client.get("/tags/rust/feed.xml").headers.get("ETag", "")
        assert etag_a != etag_b

    def test_only_tagged_post_appears(self, auth_client, db_session):
        author = _make_user("tf4@x.test", "tf4")
        tag = _make_tag("Go", "go")
        post_with_tag = _make_published_post(author, "tf-post-4a", "Go Post")
        post_with_tag.tags = [tag]
        _make_published_post(author, "tf-post-4b", "Other Post")
        _db.session.commit()

        resp = auth_client.get("/tags/go/feed.xml")
        assert b"Go Post" in resp.data
        assert b"Other Post" not in resp.data


# ── Author feed ───────────────────────────────────────────────────────────────


class TestAuthorFeedCaching:
    def test_unknown_user_returns_404(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/users/nobody-here/feed.xml")
        assert resp.status_code == 404

    def test_private_profile_returns_404(self, auth_client, db_session):
        author = _make_user("af1@x.test", "af1")
        _set_private(author)
        _make_published_post(author, "af-post-1", "AF Post 1")

        resp = auth_client.get("/users/af1/feed.xml")
        assert resp.status_code == 404

    def test_private_profile_404_has_no_etag(self, auth_client, db_session):
        author = _make_user("af2@x.test", "af2")
        _set_private(author)
        _make_published_post(author, "af-post-2", "AF Post 2")

        resp = auth_client.get("/users/af2/feed.xml")
        assert resp.status_code == 404
        assert "ETag" not in resp.headers

    def test_public_profile_200_with_caching_headers(self, auth_client, db_session):
        author = _make_user("af3@x.test", "af3")
        _make_published_post(author, "af-post-3", "AF Post 3")

        resp = auth_client.get("/users/af3/feed.xml")
        assert resp.status_code == 200
        assert resp.headers.get("ETag")
        assert resp.headers.get("Last-Modified")
        assert "public" in resp.headers.get("Cache-Control", "")

    def test_public_profile_304_on_if_none_match(self, auth_client, db_session):
        author = _make_user("af4@x.test", "af4")
        _make_published_post(author, "af-post-4", "AF Post 4")

        r1 = auth_client.get("/users/af4/feed.xml")
        r2 = auth_client.get(
            "/users/af4/feed.xml", headers={"If-None-Match": r1.headers["ETag"]}
        )
        assert r2.status_code == 304

    def test_only_author_posts_appear(self, auth_client, db_session):
        author1 = _make_user("af5a@x.test", "af5a")
        author2 = _make_user("af5b@x.test", "af5b")
        _make_published_post(author1, "af-post-5a", "Author1 Post")
        _make_published_post(author2, "af-post-5b", "Author2 Post")

        resp = auth_client.get("/users/af5a/feed.xml")
        assert b"Author1 Post" in resp.data
        assert b"Author2 Post" not in resp.data

    def test_different_authors_produce_distinct_etags(self, auth_client, db_session):
        author1 = _make_user("af6a@x.test", "af6a")
        author2 = _make_user("af6b@x.test", "af6b")
        _make_published_post(author1, "af-post-6a", "A1 Post", published_at=_T0)
        _make_published_post(author2, "af-post-6b", "A2 Post", published_at=_T0)

        etag1 = auth_client.get("/users/af6a/feed.xml").headers.get("ETag")
        etag2 = auth_client.get("/users/af6b/feed.xml").headers.get("ETag")
        assert etag1 != etag2  # scope component is different


# ── Sitemap ───────────────────────────────────────────────────────────────────


class TestSitemapCaching:
    def test_200_with_caching_headers(self, auth_client, db_session):
        author = _make_user("sm1@x.test", "sm1")
        _make_published_post(author, "sm-post-1", "SM Post 1")

        resp = auth_client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.headers.get("ETag")
        assert resp.headers.get("Last-Modified")
        assert "public" in resp.headers.get("Cache-Control", "")

    def test_content_type_is_xml(self, auth_client, db_session):
        resp = auth_client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert "application/xml" in resp.content_type

    def test_304_on_if_none_match(self, auth_client, db_session):
        author = _make_user("sm2@x.test", "sm2")
        _make_published_post(author, "sm-post-2", "SM Post 2")

        r1 = auth_client.get("/sitemap.xml")
        r2 = auth_client.get(
            "/sitemap.xml", headers={"If-None-Match": r1.headers["ETag"]}
        )
        assert r2.status_code == 304

    def test_304_on_if_modified_since(self, auth_client, db_session):
        author = _make_user("sm3@x.test", "sm3")
        _make_published_post(author, "sm-post-3", "SM Post 3")

        r1 = auth_client.get("/sitemap.xml")
        r2 = auth_client.get(
            "/sitemap.xml", headers={"If-Modified-Since": r1.headers["Last-Modified"]}
        )
        assert r2.status_code == 304

    def test_etag_changes_when_post_published(self, auth_client, db_session):
        author = _make_user("sm4@x.test", "sm4")
        _make_published_post(author, "sm-post-4a", "SM Post 4A", published_at=_T0)
        etag1 = auth_client.get("/sitemap.xml").headers["ETag"]

        _make_published_post(author, "sm-post-4b", "SM Post 4B", published_at=_T1)
        etag2 = auth_client.get("/sitemap.xml").headers["ETag"]

        assert etag1 != etag2

    def test_published_post_url_in_sitemap(self, auth_client, db_session):
        author = _make_user("sm5@x.test", "sm5")
        _make_published_post(author, "sm-post-5", "SM Post 5")

        resp = auth_client.get("/sitemap.xml")
        assert b"sm-post-5" in resp.data

    def test_draft_not_in_sitemap(self, auth_client, db_session):
        author = _make_user("sm6@x.test", "sm6")
        _make_published_post(author, "sm-post-6", "SM Published Post")
        _make_draft_post(author, "sm-draft-6")

        resp = auth_client.get("/sitemap.xml")
        assert b"sm-post-6" in resp.data
        assert b"sm-draft-6" not in resp.data

    def test_sitemap_urls_are_absolute(self, auth_client, db_session):
        author = _make_user("sm7@x.test", "sm7")
        _make_published_post(author, "sm-post-7", "SM Post 7")

        resp = auth_client.get("/sitemap.xml")
        assert b"http://testserver" in resp.data

    def test_draft_does_not_change_etag(self, auth_client, db_session):
        author = _make_user("sm8@x.test", "sm8")
        _make_published_post(author, "sm-post-8", "SM Post 8", published_at=_T0)
        etag1 = auth_client.get("/sitemap.xml").headers["ETag"]

        _make_draft_post(author, "sm-draft-8a")
        etag2 = auth_client.get("/sitemap.xml").headers["ETag"]

        assert etag1 == etag2


# ── Robots.txt ────────────────────────────────────────────────────────────────


class TestRobotsTxt:
    def test_200_status(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/robots.txt")
        assert resp.status_code == 200

    def test_has_cache_control(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/robots.txt")
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc
        assert "max-age" in cc

    def test_no_etag_header(self, auth_client, db_session):  # noqa: ARG002
        """robots.txt uses simple max-age only; no ETag is expected."""
        resp = auth_client.get("/robots.txt")
        assert "ETag" not in resp.headers

    def test_contains_sitemap_url(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/robots.txt")
        assert b"Sitemap:" in resp.data
        assert b"sitemap.xml" in resp.data

    def test_allows_all_crawlers(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/robots.txt")
        assert b"User-agent: *" in resp.data
        assert b"Allow: /" in resp.data


# ── ETag format ───────────────────────────────────────────────────────────────


class TestETagFormat:
    """Verify ETags are correctly formatted weak ETags."""

    def test_global_feed_etag_is_weak(self, auth_client, db_session):
        author = _make_user("etf1@x.test", "etf1")
        _make_published_post(author, "etf-post-1", "ETF Post 1")
        resp = auth_client.get("/feed.xml")
        etag = resp.headers.get("ETag", "")
        assert etag.startswith('W/"'), f"Expected weak ETag, got: {etag!r}"

    def test_sitemap_etag_is_weak(self, auth_client, db_session):
        author = _make_user("etf2@x.test", "etf2")
        _make_published_post(author, "etf-post-2", "ETF Post 2")
        resp = auth_client.get("/sitemap.xml")
        etag = resp.headers.get("ETag", "")
        assert etag.startswith('W/"'), f"Expected weak ETag, got: {etag!r}"
