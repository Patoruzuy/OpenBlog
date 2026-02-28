"""Tests for the "what changed since last visit" read-history feature.

Covers:
  - Visiting a published post as an authenticated user creates a UserPostRead record
  - The stored version matches the post's current version
  - "Updated since you last read" banner appears when post.version > last_read_version
  - Banner disappears after the user re-reads the updated post
  - Anonymous visits do NOT create UserPostRead records
  - ReadHistoryService.get_updated_post_ids() correctly identifies stale reads
  - The "Updated" badge appears on the post list for updated posts
  - Re-visiting a post does not create duplicate records (upsert semantics)
"""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.user_post_read import UserPostRead
from backend.services.read_history_service import ReadHistoryService

# ── Helpers ────────────────────────────────────────────────────────────────────


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _make_published_post(author, title: str = "Test Article", slug: str | None = None) -> Post:
    post = Post(
        title=title,
        slug=slug or title.lower().replace(" ", "-"),
        markdown_body="Some content.",
        author_id=author.id,
        status=PostStatus.published,
        version=1,
    )
    db.session.add(post)
    db.session.commit()
    return post


def _get_read_record(user_id: int, post_id: int) -> UserPostRead | None:
    from sqlalchemy import select
    return db.session.scalar(
        select(UserPostRead).where(
            UserPostRead.user_id == user_id,
            UserPostRead.post_id == post_id,
        )
    )


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def alice(make_user_token, db_session):  # noqa: ARG001
    user, _tok = make_user_token("alice@rh.com", "alice_rh")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):  # noqa: ARG001
    user, _tok = make_user_token("bob@rh.com", "bob_rh")
    return user


# ── Record creation ───────────────────────────────────────────────────────────

class TestReadRecordCreation:
    def test_visit_creates_read_record(self, auth_client, alice):
        post = _make_published_post(alice, title="Create Record", slug="create-record")
        _login(auth_client, alice.id)
        resp = auth_client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200
        record = _get_read_record(alice.id, post.id)
        assert record is not None

    def test_read_version_equals_post_version(self, auth_client, alice):
        post = _make_published_post(alice, title="Version Match", slug="version-match")
        post.version = 3
        db.session.commit()
        _login(auth_client, alice.id)
        auth_client.get(f"/posts/{post.slug}")
        record = _get_read_record(alice.id, post.id)
        assert record is not None
        assert record.last_read_version == 3

    def test_last_read_at_is_set(self, auth_client, alice):
        post = _make_published_post(alice, title="Timestamp Set", slug="timestamp-set")
        _login(auth_client, alice.id)
        auth_client.get(f"/posts/{post.slug}")
        record = _get_read_record(alice.id, post.id)
        assert record is not None
        assert record.last_read_at is not None

    def test_anonymous_visit_creates_no_record(self, auth_client, alice, app):
        post = _make_published_post(alice, title="Anon Visit", slug="anon-visit")
        resp = auth_client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200
        with app.app_context():
            from sqlalchemy import select
            from backend.models.user_post_read import UserPostRead as UPR
            count = db.session.scalar(
                select(db.func.count(UPR.id)).where(UPR.post_id == post.id)
            )
            assert count == 0

    def test_second_visit_does_not_create_duplicate(self, auth_client, alice, app):
        post = _make_published_post(alice, title="No Dup", slug="no-dup")
        _login(auth_client, alice.id)
        auth_client.get(f"/posts/{post.slug}")
        auth_client.get(f"/posts/{post.slug}")
        with app.app_context():
            from sqlalchemy import select
            from backend.models.user_post_read import UserPostRead as UPR
            count = db.session.scalar(
                select(db.func.count(UPR.id)).where(
                    UPR.user_id == alice.id,
                    UPR.post_id == post.id,
                )
            )
            assert count == 1

    def test_different_users_get_separate_records(self, auth_client, alice, bob):
        post = _make_published_post(alice, title="Two Readers", slug="two-readers")
        # Alice visits
        _login(auth_client, alice.id)
        auth_client.get(f"/posts/{post.slug}")
        # Bob visits
        _login(auth_client, bob.id)
        auth_client.get(f"/posts/{post.slug}")

        assert _get_read_record(alice.id, post.id) is not None
        assert _get_read_record(bob.id, post.id) is not None


# ── "Updated since last read" banner ─────────────────────────────────────────

class TestUpdatedBanner:
    def test_no_banner_on_first_visit(self, auth_client, alice):
        post = _make_published_post(alice, title="First Banner", slug="first-banner")
        _login(auth_client, alice.id)
        resp = auth_client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200
        assert b"Updated since you last read" not in resp.data

    def test_banner_appears_after_version_bump(self, auth_client, alice):
        post = _make_published_post(alice, title="Banner Bump", slug="banner-bump")
        _login(auth_client, alice.id)
        # First visit — no banner, record stored at version 1
        auth_client.get(f"/posts/{post.slug}")
        # Simulate a revision acceptance bumping the version
        post.version = 2
        db.session.commit()
        # Second visit — banner should appear
        resp = auth_client.get(f"/posts/{post.slug}")
        assert b"Updated since you last read" in resp.data

    def test_banner_shows_old_and_new_version(self, auth_client, alice):
        post = _make_published_post(alice, title="Version Numbers", slug="version-numbers")
        _login(auth_client, alice.id)
        auth_client.get(f"/posts/{post.slug}")
        post.version = 4
        db.session.commit()
        resp = auth_client.get(f"/posts/{post.slug}")
        body = resp.data.decode()
        assert "v1" in body  # last_read_version
        assert "v4" in body  # current version

    def test_banner_disappears_after_re_read(self, auth_client, alice):
        post = _make_published_post(alice, title="Banner Gone", slug="banner-gone")
        _login(auth_client, alice.id)
        auth_client.get(f"/posts/{post.slug}")   # visit 1 → record at v1
        post.version = 2
        db.session.commit()
        auth_client.get(f"/posts/{post.slug}")   # visit 2 → banner shown, record updated to v2
        resp = auth_client.get(f"/posts/{post.slug}")  # visit 3 → no banner
        assert b"Updated since you last read" not in resp.data

    def test_banner_record_updated_to_current_version(self, auth_client, alice):
        post = _make_published_post(alice, title="Record Updated", slug="record-updated")
        _login(auth_client, alice.id)
        auth_client.get(f"/posts/{post.slug}")   # v1 stored
        post.version = 3
        db.session.commit()
        auth_client.get(f"/posts/{post.slug}")   # banner shown, record updated → v3
        record = _get_read_record(alice.id, post.id)
        assert record.last_read_version == 3

    def test_no_banner_for_anonymous_user(self, auth_client, alice):
        post = _make_published_post(alice, title="Anon No Banner", slug="anon-no-banner")
        # Anonymous: no session
        resp = auth_client.get(f"/posts/{post.slug}")
        assert b"Updated since you last read" not in resp.data


# ── ReadHistoryService unit tests ─────────────────────────────────────────────

class TestReadHistoryService:
    def test_get_read_returns_none_before_first_visit(self, alice, db_session):  # noqa: ARG001
        post = _make_published_post(alice, title="No Read Yet", slug="no-read-yet")
        result = ReadHistoryService.get_read(alice.id, post.id)
        assert result is None

    def test_record_read_creates_row(self, alice, db_session):  # noqa: ARG001
        post = _make_published_post(alice, title="Service Create", slug="service-create")
        record = ReadHistoryService.record_read(alice.id, post)
        assert record.user_id == alice.id
        assert record.post_id == post.id
        assert record.last_read_version == post.version

    def test_record_read_updates_existing_row(self, alice, db_session):  # noqa: ARG001
        post = _make_published_post(alice, title="Service Update", slug="service-update")
        ReadHistoryService.record_read(alice.id, post)
        post.version = 5
        db.session.commit()
        updated = ReadHistoryService.record_read(alice.id, post)
        assert updated.last_read_version == 5

    def test_get_updated_post_ids_empty_when_no_reads(self, alice, db_session):  # noqa: ARG001
        post = _make_published_post(alice, title="No Reads Updated", slug="no-reads-updated")
        result = ReadHistoryService.get_updated_post_ids(alice.id, [post])
        assert result == set()

    def test_get_updated_post_ids_empty_when_version_same(self, alice, db_session):  # noqa: ARG001
        post = _make_published_post(alice, title="Same Version", slug="same-version")
        ReadHistoryService.record_read(alice.id, post)
        result = ReadHistoryService.get_updated_post_ids(alice.id, [post])
        assert result == set()

    def test_get_updated_post_ids_returns_id_when_version_bumped(self, alice, db_session):  # noqa: ARG001
        post = _make_published_post(alice, title="Bumped Version", slug="bumped-version")
        ReadHistoryService.record_read(alice.id, post)
        post.version = 2
        db.session.commit()
        result = ReadHistoryService.get_updated_post_ids(alice.id, [post])
        assert post.id in result

    def test_get_updated_post_ids_handles_empty_list(self, alice, db_session):  # noqa: ARG001
        result = ReadHistoryService.get_updated_post_ids(alice.id, [])
        assert result == set()

    def test_get_updated_post_ids_mixed_posts(self, alice, db_session):  # noqa: ARG001
        """Only posts with version > last_read_version appear in the result."""
        p1 = _make_published_post(alice, title="Mixed P1", slug="mixed-p1")
        p2 = _make_published_post(alice, title="Mixed P2", slug="mixed-p2")
        p3 = _make_published_post(alice, title="Mixed P3", slug="mixed-p3")

        # Read all at version 1
        ReadHistoryService.record_read(alice.id, p1)
        ReadHistoryService.record_read(alice.id, p2)
        # p3: never read

        # Bump only p2
        p2.version = 3
        db.session.commit()

        result = ReadHistoryService.get_updated_post_ids(alice.id, [p1, p2, p3])
        assert p2.id in result
        assert p1.id not in result   # read at same version
        assert p3.id not in result   # never read (no record)


# ── "Updated" badge on post list ─────────────────────────────────────────────

class TestUpdatedBadgeOnList:
    def test_updated_badge_shown_for_stale_read(self, auth_client, alice):
        post = _make_published_post(alice, title="List Badge", slug="list-badge")
        # Record a read at v1
        ReadHistoryService.record_read(alice.id, post)
        # Bump version
        post.version = 2
        db.session.commit()
        # Request the list
        _login(auth_client, alice.id)
        resp = auth_client.get("/posts/")
        assert b"Updated" in resp.data

    def test_updated_badge_absent_when_current(self, auth_client, alice):
        post = _make_published_post(alice, title="No List Badge", slug="no-list-badge")
        # Read at v1, version still v1
        ReadHistoryService.record_read(alice.id, post)
        _login(auth_client, alice.id)
        resp = auth_client.get("/posts/")
        # The badge text "Updated" should not appear as an explicit element
        assert b'badge--updated' not in resp.data

    def test_no_badge_for_anonymous_on_list(self, auth_client, alice):
        post = _make_published_post(alice, title="Anon List Badge", slug="anon-list-badge")
        ReadHistoryService.record_read(alice.id, post)
        post.version = 2
        db.session.commit()
        # Anonymous request — no session
        resp = auth_client.get("/posts/")
        assert b'badge--updated' not in resp.data
