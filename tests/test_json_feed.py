"""JSON Feed v1.1 tests.

Covers:
  - 200 with correct Content-Type (application/feed+json)
  - Required top-level fields (version, title, home_page_url, feed_url, items)
  - Items contain required per-item fields
  - Only published posts included; drafts excluded
  - Absolute URLs in item ``id`` and ``url``
  - No email addresses leaked
  - Tag feed filters correctly; 404 for unknown tag
  - Author feed respects privacy; 404 for private/unknown
  - ETag and Last-Modified caching headers present on 200
  - 304 on If-None-Match
  - 304 on If-Modified-Since
  - 200 on stale ETag (no false 304)
  - ETag changes when new post is published
  - Draft posts do NOT affect fingerprints
  - ETags are weak (start with W/")
  - JSON Feed ETags are distinct from RSS ETags (different kind prefix)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.portal import ProfileVisibility, UserPrivacySettings
from backend.models.post import Post, PostStatus
from backend.models.tag import Tag
from backend.models.user import User
from backend.services.auth_service import AuthService

# ── Helpers ───────────────────────────────────────────────────────────────────

_T0 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 6, 1, 13, 0, 0, tzinfo=UTC)


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


def _make_draft_post(author: User, slug: str = "jf-draft") -> Post:
    post = Post(
        slug=slug,
        title="Draft Post (should never appear)",
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


def _json(resp) -> dict:
    return json.loads(resp.data.decode())


# ── Global feed — content ─────────────────────────────────────────────────────


class TestGlobalJsonFeedContent:
    def test_200_status(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/feed.json")
        assert resp.status_code == 200

    def test_content_type(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/feed.json")
        assert "application/feed+json" in resp.content_type

    def test_version_field(self, auth_client, db_session):  # noqa: ARG002
        data = _json(auth_client.get("/feed.json"))
        assert data["version"] == "https://jsonfeed.org/version/1.1"

    def test_required_top_level_fields(self, auth_client, db_session):  # noqa: ARG002
        data = _json(auth_client.get("/feed.json"))
        for field in ("version", "title", "home_page_url", "feed_url", "items"):
            assert field in data, f"missing top-level field: {field}"

    def test_home_page_url_is_absolute(self, auth_client, db_session):  # noqa: ARG002
        data = _json(auth_client.get("/feed.json"))
        assert data["home_page_url"].startswith("http://testserver")

    def test_feed_url_is_absolute(self, auth_client, db_session):  # noqa: ARG002
        data = _json(auth_client.get("/feed.json"))
        assert data["feed_url"].startswith("http://testserver")
        assert data["feed_url"].endswith("/feed.json")

    def test_published_post_appears(self, auth_client, db_session):
        author = _make_user("jf1@x.test", "jf1")
        _make_published_post(author, "jf-pub-1", "Published JSON Post")
        data = _json(auth_client.get("/feed.json"))
        titles = [item["title"] for item in data["items"]]
        assert "Published JSON Post" in titles

    def test_draft_excluded(self, auth_client, db_session):
        author = _make_user("jf2@x.test", "jf2")
        _make_published_post(author, "jf-pub-2", "Real JSON Post")
        _make_draft_post(author, "jf-draft-2")
        data = _json(auth_client.get("/feed.json"))
        titles = [item["title"] for item in data["items"]]
        assert "Draft Post (should never appear)" not in titles

    def test_item_id_is_absolute_url(self, auth_client, db_session):
        author = _make_user("jf3@x.test", "jf3")
        _make_published_post(author, "jf-abs-3", "Absolute ID Post")
        data = _json(auth_client.get("/feed.json"))
        item = next(i for i in data["items"] if i["title"] == "Absolute ID Post")
        assert item["id"].startswith("http://testserver/posts/jf-abs-3")

    def test_item_url_is_absolute_url(self, auth_client, db_session):
        author = _make_user("jf4@x.test", "jf4")
        _make_published_post(author, "jf-abs-4", "Absolute URL Post")
        data = _json(auth_client.get("/feed.json"))
        item = next(i for i in data["items"] if i["title"] == "Absolute URL Post")
        assert item["url"].startswith("http://testserver/posts/jf-abs-4")

    def test_item_id_equals_url(self, auth_client, db_session):
        author = _make_user("jf5@x.test", "jf5")
        _make_published_post(author, "jf-idurl-5", "ID==URL Post")
        data = _json(auth_client.get("/feed.json"))
        item = next(i for i in data["items"] if "idurl" in i["id"])
        assert item["id"] == item["url"]

    def test_item_required_fields(self, auth_client, db_session):
        author = _make_user("jf6@x.test", "jf6")
        _make_published_post(author, "jf-fields-6", "Fields Post")
        data = _json(auth_client.get("/feed.json"))
        item = next(i for i in data["items"] if i["title"] == "Fields Post")
        for field in (
            "id",
            "url",
            "title",
            "content_text",
            "date_published",
            "authors",
        ):
            assert field in item, f"missing item field: {field}"

    def test_authors_no_email(self, auth_client, db_session):
        author = _make_user("secret-jf@private.test", "jfsecret")
        _make_published_post(author, "jf-secret-7", "Private Email Post")
        resp = auth_client.get("/feed.json")
        # The raw response bytes must not contain the email
        assert b"secret-jf@private.test" not in resp.data

    def test_authors_array_has_name(self, auth_client, db_session):
        author = _make_user("jf8@x.test", "jf8")
        _make_published_post(author, "jf-author-8", "Author Name Post")
        data = _json(auth_client.get("/feed.json"))
        item = next(i for i in data["items"] if i["title"] == "Author Name Post")
        assert isinstance(item["authors"], list)
        assert "name" in item["authors"][0]

    def test_date_published_is_iso8601(self, auth_client, db_session):
        author = _make_user("jf9@x.test", "jf9")
        _make_published_post(author, "jf-date-9", "Date Post")
        data = _json(auth_client.get("/feed.json"))
        item = next(i for i in data["items"] if i["title"] == "Date Post")
        # Must end in Z (UTC) and be parseable
        assert item["date_published"].endswith("Z")
        datetime.fromisoformat(item["date_published"].replace("Z", "+00:00"))

    def test_tags_array(self, auth_client, db_session):
        author = _make_user("jf10@x.test", "jf10")
        tag = _make_tag("Science", "science-jf")
        _make_published_post(author, "jf-tagged-10", "Tagged Post", tags=[tag])
        data = _json(auth_client.get("/feed.json"))
        item = next(i for i in data["items"] if i["title"] == "Tagged Post")
        assert "Science" in item["tags"]

    def test_items_list_is_list(self, auth_client, db_session):  # noqa: ARG002
        data = _json(auth_client.get("/feed.json"))
        assert isinstance(data["items"], list)


# ── Tag feed ──────────────────────────────────────────────────────────────────


class TestTagJsonFeed:
    def test_404_for_unknown_tag(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/tags/no-such-tag/feed.json")
        assert resp.status_code == 404

    def test_200_for_known_tag(self, auth_client, db_session):
        author = _make_user("jft1@x.test", "jft1")
        tag = _make_tag("Python", "python-jf")
        _make_published_post(author, "jft-post-1", "Python Post", tags=[tag])
        resp = auth_client.get("/tags/python-jf/feed.json")
        assert resp.status_code == 200

    def test_only_tagged_posts_included(self, auth_client, db_session):
        author = _make_user("jft2@x.test", "jft2")
        tag = _make_tag("Go", "go-jf")
        _make_published_post(author, "jft-go-2", "Go Post", tags=[tag])
        _make_published_post(author, "jft-other-2", "Other Post")
        data = _json(auth_client.get("/tags/go-jf/feed.json"))
        titles = [i["title"] for i in data["items"]]
        assert "Go Post" in titles
        assert "Other Post" not in titles

    def test_feed_url_correct(self, auth_client, db_session):
        author = _make_user("jft3@x.test", "jft3")
        tag = _make_tag("Java", "java-jf")
        _make_published_post(author, "jft-java-3", "Java Post", tags=[tag])
        data = _json(auth_client.get("/tags/java-jf/feed.json"))
        assert "java-jf" in data["feed_url"]
        assert data["feed_url"].startswith("http://testserver")

    def test_content_type(self, auth_client, db_session):
        author = _make_user("jft4@x.test", "jft4")
        tag = _make_tag("Rust", "rust-jf")
        _make_published_post(author, "jft-rust-4", "Rust Post", tags=[tag])
        resp = auth_client.get("/tags/rust-jf/feed.json")
        assert "application/feed+json" in resp.content_type


# ── Author feed ───────────────────────────────────────────────────────────────


class TestAuthorJsonFeed:
    def test_404_for_unknown_user(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/users/no-such-user/feed.json")
        assert resp.status_code == 404

    def test_404_for_private_profile(self, auth_client, db_session):
        author = _make_user("jfa1@x.test", "jfa1")
        _set_private(author)
        resp = auth_client.get("/users/jfa1/feed.json")
        assert resp.status_code == 404

    def test_200_for_public_profile(self, auth_client, db_session):
        author = _make_user("jfa2@x.test", "jfa2")
        _make_published_post(author, "jfa-pub-2", "Author Post")
        resp = auth_client.get("/users/jfa2/feed.json")
        assert resp.status_code == 200

    def test_only_author_posts_included(self, auth_client, db_session):
        author1 = _make_user("jfa3a@x.test", "jfa3a")
        author2 = _make_user("jfa3b@x.test", "jfa3b")
        _make_published_post(author1, "jfa-post-a3", "Author1 Post")
        _make_published_post(author2, "jfa-post-b3", "Author2 Post")
        data = _json(auth_client.get("/users/jfa3a/feed.json"))
        titles = [i["title"] for i in data["items"]]
        assert "Author1 Post" in titles
        assert "Author2 Post" not in titles

    def test_no_etag_on_404(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/users/ghost-jf/feed.json")
        assert resp.status_code == 404
        assert not resp.headers.get("ETag")


# ── HTTP caching ──────────────────────────────────────────────────────────────


class TestGlobalJsonFeedCaching:
    def test_200_has_etag(self, auth_client, db_session):
        author = _make_user("jfc1@x.test", "jfc1")
        _make_published_post(author, "jfc-post-1", "JFC Post 1")
        resp = auth_client.get("/feed.json")
        assert resp.status_code == 200
        assert resp.headers.get("ETag")

    def test_200_has_last_modified(self, auth_client, db_session):
        author = _make_user("jfc2@x.test", "jfc2")
        _make_published_post(author, "jfc-post-2", "JFC Post 2")
        resp = auth_client.get("/feed.json")
        assert resp.headers.get("Last-Modified")

    def test_200_has_cache_control_public(self, auth_client, db_session):
        author = _make_user("jfc3@x.test", "jfc3")
        _make_published_post(author, "jfc-post-3", "JFC Post 3")
        resp = auth_client.get("/feed.json")
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc
        assert "max-age" in cc

    def test_304_on_if_none_match(self, auth_client, db_session):
        author = _make_user("jfc4@x.test", "jfc4")
        _make_published_post(author, "jfc-post-4", "JFC Post 4")
        r1 = auth_client.get("/feed.json")
        etag = r1.headers["ETag"]
        r2 = auth_client.get("/feed.json", headers={"If-None-Match": etag})
        assert r2.status_code == 304
        assert r2.headers.get("ETag") == etag
        assert r2.headers.get("Cache-Control")

    def test_304_body_is_empty(self, auth_client, db_session):
        author = _make_user("jfc5@x.test", "jfc5")
        _make_published_post(author, "jfc-post-5", "JFC Post 5")
        r1 = auth_client.get("/feed.json")
        etag = r1.headers["ETag"]
        r2 = auth_client.get("/feed.json", headers={"If-None-Match": etag})
        assert r2.status_code == 304
        assert len(r2.data) == 0

    def test_304_on_if_modified_since(self, auth_client, db_session):
        author = _make_user("jfc6@x.test", "jfc6")
        _make_published_post(author, "jfc-post-6", "JFC Post 6")
        r1 = auth_client.get("/feed.json")
        last_mod = r1.headers["Last-Modified"]
        r2 = auth_client.get("/feed.json", headers={"If-Modified-Since": last_mod})
        assert r2.status_code == 304

    def test_200_on_stale_etag(self, auth_client, db_session):
        author = _make_user("jfc7@x.test", "jfc7")
        _make_published_post(author, "jfc-post-7", "JFC Post 7")
        resp = auth_client.get("/feed.json", headers={"If-None-Match": 'W/"stale-jf"'})
        assert resp.status_code == 200

    def test_etag_changes_on_new_post(self, auth_client, db_session):
        author = _make_user("jfc8@x.test", "jfc8")
        _make_published_post(author, "jfc-post-8a", "JFC Post 8A", published_at=_T0)
        etag1 = auth_client.get("/feed.json").headers["ETag"]
        _make_published_post(author, "jfc-post-8b", "JFC Post 8B", published_at=_T1)
        etag2 = auth_client.get("/feed.json").headers["ETag"]
        assert etag1 != etag2

    def test_draft_does_not_change_etag(self, auth_client, db_session):
        author = _make_user("jfc9@x.test", "jfc9")
        _make_published_post(author, "jfc-post-9", "JFC Post 9", published_at=_T0)
        etag1 = auth_client.get("/feed.json").headers["ETag"]
        _make_draft_post(author, "jfc-draft-9")
        etag2 = auth_client.get("/feed.json").headers["ETag"]
        assert etag1 == etag2

    def test_etag_is_weak(self, auth_client, db_session):
        author = _make_user("jfc10@x.test", "jfc10")
        _make_published_post(author, "jfc-post-10", "JFC Post 10")
        etag = auth_client.get("/feed.json").headers["ETag"]
        assert etag.startswith('W/"')

    def test_etag_distinct_from_rss_etag(self, auth_client, db_session):
        """JSON Feed and RSS ETags must be independent (different kind prefix)."""
        author = _make_user("jfc11@x.test", "jfc11")
        _make_published_post(author, "jfc-post-11", "JFC Post 11")
        json_etag = auth_client.get("/feed.json").headers["ETag"]
        rss_etag = auth_client.get("/feed.xml").headers["ETag"]
        assert json_etag != rss_etag


class TestTagJsonFeedCaching:
    def test_200_has_etag(self, auth_client, db_session):
        author = _make_user("jftc1@x.test", "jftc1")
        tag = _make_tag("Cache", "cache-jf")
        _make_published_post(author, "jftc-post-1", "Cache Post", tags=[tag])
        resp = auth_client.get("/tags/cache-jf/feed.json")
        assert resp.status_code == 200
        assert resp.headers.get("ETag")

    def test_304_on_if_none_match(self, auth_client, db_session):
        author = _make_user("jftc2@x.test", "jftc2")
        tag = _make_tag("Cache2", "cache2-jf")
        _make_published_post(author, "jftc-post-2", "Cache2 Post", tags=[tag])
        r1 = auth_client.get("/tags/cache2-jf/feed.json")
        r2 = auth_client.get(
            "/tags/cache2-jf/feed.json", headers={"If-None-Match": r1.headers["ETag"]}
        )
        assert r2.status_code == 304

    def test_scope_isolated_from_global(self, auth_client, db_session):
        author = _make_user("jftc3@x.test", "jftc3")
        tag = _make_tag("Isolated", "isolated-jf")
        _make_published_post(author, "jftc-post-3", "Isolated Post", tags=[tag])
        tag_etag = auth_client.get("/tags/isolated-jf/feed.json").headers["ETag"]
        global_etag = auth_client.get("/feed.json").headers["ETag"]
        assert tag_etag != global_etag


class TestAuthorJsonFeedCaching:
    def test_200_has_etag(self, auth_client, db_session):
        author = _make_user("jfac1@x.test", "jfac1")
        _make_published_post(author, "jfac-post-1", "Author Cache Post")
        resp = auth_client.get("/users/jfac1/feed.json")
        assert resp.status_code == 200
        assert resp.headers.get("ETag")

    def test_304_on_if_none_match(self, auth_client, db_session):
        author = _make_user("jfac2@x.test", "jfac2")
        _make_published_post(author, "jfac-post-2", "Author 304 Post")
        r1 = auth_client.get("/users/jfac2/feed.json")
        r2 = auth_client.get(
            "/users/jfac2/feed.json", headers={"If-None-Match": r1.headers["ETag"]}
        )
        assert r2.status_code == 304

    def test_no_etag_on_private_404(self, auth_client, db_session):
        author = _make_user("jfac3@x.test", "jfac3")
        _set_private(author)
        resp = auth_client.get("/users/jfac3/feed.json")
        assert resp.status_code == 404
        assert not resp.headers.get("ETag")
