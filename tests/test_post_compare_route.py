"""Tests for the GET /posts/<slug>/compare route."""

from __future__ import annotations

from flask import url_for

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.user import User, UserRole

# ── Helpers ───────────────────────────────────────────────────────────────────


def _user(db, suffix="a", role="contributor") -> User:
    u = User(
        email=f"cmp_{suffix}@test.com",
        username=f"cmp_{suffix}",
        password_hash="x",
        role=UserRole(role),
    )
    db.session.add(u)
    db.session.commit()
    return u


def _post(
    db,
    author: User,
    *,
    slug="compare-post",
    version: int = 2,
    status=PostStatus.published,
) -> Post:
    p = Post(
        title="Compare Post",
        slug=slug,
        markdown_body="# Current\n\nLatest body.",
        author_id=author.id,
        status=status,
        version=version,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _snapshot(db, post: Post, version_number: int, body: str) -> PostVersion:
    snap = PostVersion(
        post_id=post.id,
        version_number=version_number,
        markdown_body=body,
    )
    db.session.add(snap)
    db.session.commit()
    return snap


def _login(client, user: User) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── url_for resolution ────────────────────────────────────────────────────────


class TestCompareUrlFor:
    def test_url_resolves(self, app, db_session):
        with app.test_request_context():
            url = url_for("posts.compare", slug="my-post", **{"from": 1}, to=2)
        assert url == "/posts/my-post/compare?from=1&to=2"

    def test_context_param_included(self, app, db_session):
        with app.test_request_context():
            url = url_for("posts.compare", slug="x", **{"from": 1}, to=2, context=5)
        assert "context=5" in url


# ── Parameter validation ──────────────────────────────────────────────────────


class TestCompareValidation:
    def _setup(self, db):
        u = _user(db)
        p = _post(db, u, version=3)
        _snapshot(db, p, 2, "# v2\n\nOld.")
        _snapshot(db, p, 3, "# v3\n\nNew.")
        return p

    def test_missing_from_returns_400(self, client, db_session):
        p = self._setup(_db)
        resp = client.get(f"/posts/{p.slug}/compare?to=2")
        assert resp.status_code == 400

    def test_to_defaults_to_post_version(self, client, db_session):
        """Omitting 'to' should compare from_v to the current version."""
        p = self._setup(_db)  # version=3, snapshots for v2 and v3
        resp = client.get(f"/posts/{p.slug}/compare?from=2")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "v3" in body  # to_version should be post.version (3)

    def test_from_equals_post_version_without_to_returns_400(self, client, db_session):
        """?from=3 with post.version=3 → to=3, from==to → 400."""
        p = self._setup(_db)
        resp = client.get(f"/posts/{p.slug}/compare?from=3")
        assert resp.status_code == 400

    def test_equal_versions_returns_400(self, client, db_session):
        p = self._setup(_db)
        resp = client.get(f"/posts/{p.slug}/compare?from=2&to=2")
        assert resp.status_code == 400

    def test_to_beyond_current_version_returns_400(self, client, db_session):
        p = self._setup(_db)
        resp = client.get(f"/posts/{p.slug}/compare?from=1&to=99")
        assert resp.status_code == 400

    def test_from_zero_returns_400(self, client, db_session):
        p = self._setup(_db)
        resp = client.get(f"/posts/{p.slug}/compare?from=0&to=2")
        assert resp.status_code == 400

    def test_nonexistent_slug_returns_404(self, client, db_session):
        resp = client.get("/posts/does-not-exist/compare?from=1&to=2")
        assert resp.status_code == 404

    def test_draft_hidden_from_anonymous(self, client, db_session):
        u = _user(_db, "anon")
        p = _post(_db, u, slug="draft-cmp", version=2, status=PostStatus.draft)
        _snapshot(_db, p, 2, "v2")
        resp = client.get(f"/posts/{p.slug}/compare?from=1&to=2")
        assert resp.status_code == 404

    def test_invalid_non_integer_param_returns_400(self, client, db_session):
        p = self._setup(_db)
        resp = client.get(f"/posts/{p.slug}/compare?from=abc&to=2")
        assert resp.status_code == 400


# ── Happy path ────────────────────────────────────────────────────────────────


class TestCompareHappyPath:
    def _setup(self, db, suffix="hp"):
        u = _user(db, suffix)
        p = _post(db, u, slug=f"happy-{suffix}", version=3)
        _snapshot(db, p, 2, "# Hello\n\nOld content.")
        _snapshot(db, p, 3, "# Hello\n\nNew content.")
        return p

    def test_returns_200(self, client, db_session):
        p = self._setup(_db)
        resp = client.get(f"/posts/{p.slug}/compare?from=2&to=3")
        assert resp.status_code == 200

    def test_response_contains_version_numbers(self, client, db_session):
        p = self._setup(_db, "vn")
        body = client.get(f"/posts/{p.slug}/compare?from=2&to=3").data.decode()
        assert "v2" in body
        assert "v3" in body

    def test_diff_table_present(self, client, db_session):
        p = self._setup(_db, "dt")
        body = client.get(f"/posts/{p.slug}/compare?from=2&to=3").data.decode()
        assert "diff-table" in body

    def test_from_greater_than_to_is_auto_swapped(self, client, db_session):
        """from=3&to=2 should succeed and show the swap notice."""
        p = self._setup(_db, "sw")
        resp = client.get(f"/posts/{p.slug}/compare?from=3&to=2")
        assert resp.status_code == 200
        body = resp.data.decode()
        # Swap notice must appear somewhere in the rendered page
        assert "swapped" in body.lower()

    def test_no_swap_notice_when_order_correct(self, client, db_session):
        """No swap notice when from < to."""
        p = self._setup(_db, "nosw")
        body = client.get(f"/posts/{p.slug}/compare?from=2&to=3").data.decode()
        assert "swapped" not in body.lower()

    def test_custom_context_param_accepted(self, client, db_session):
        p = self._setup(_db, "ctx")
        resp = client.get(f"/posts/{p.slug}/compare?from=2&to=3&context=5")
        assert resp.status_code == 200


# ── Missing snapshot ──────────────────────────────────────────────────────────


class TestCompareMissingSnapshot:
    def test_missing_version_shows_notice(self, client, db_session):
        u = _user(_db, "ms")
        p = _post(_db, u, slug="missing-snap", version=2)
        # Only snapshot for v2 exists; v1 has no snapshot
        _snapshot(_db, p, 2, "# v2")
        resp = client.get(f"/posts/{p.slug}/compare?from=1&to=2")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "not available" in body.lower() or "snapshot" in body.lower()


# ── Banner CTA link ───────────────────────────────────────────────────────────


class TestBannerCTALink:
    """The 'Updated since you last read' banner should link to the compare URL."""

    def test_stale_banner_links_to_compare(self, client, db_session):
        from backend.models.user_post_read import UserPostRead

        u = _user(_db, "banner")
        p = _post(_db, u, slug="banner-test", version=3)
        _snapshot(_db, p, 2, "# Old")
        _snapshot(_db, p, 3, "# New")

        _login(client, u)

        # Seed a read record for version 2 so the banner fires
        record = UserPostRead(user_id=u.id, post_id=p.id, last_read_version=2)
        _db.session.add(record)
        _db.session.commit()

        body = client.get(f"/posts/{p.slug}").data.decode()
        assert f"/posts/{p.slug}/compare" in body
        assert "from=2" in body
        assert "to=3" in body
