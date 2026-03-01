"""Unit tests for PostVersionService."""

from __future__ import annotations

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.user import User, UserRole
from backend.services.post_version_service import PostVersionService

# ── Helpers ───────────────────────────────────────────────────────────────────


def _user(db, suffix="a") -> User:
    u = User(
        email=f"user_{suffix}@test.com",
        username=f"user_{suffix}",
        password_hash="x",
        role=UserRole.contributor,
    )
    db.session.add(u)
    db.session.commit()
    return u


def _post(db, author: User, *, version: int = 2) -> Post:
    p = Post(
        title="Test Post",
        slug="test-post",
        markdown_body="# Current\n\nCurrent body.",
        author_id=author.id,
        status=PostStatus.published,
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


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestGetMarkdownForVersion:
    def test_returns_stored_snapshot(self, app, db_session):
        with app.app_context():
            u = _user(_db)
            p = _post(_db, u, version=3)
            _snapshot(_db, p, 2, "# Version 2\n\nOld body.")
            result = PostVersionService.get_markdown_for_version(p.id, 2)
        assert result == "# Version 2\n\nOld body."

    def test_fallback_for_current_version_without_snapshot(self, app, db_session):
        """If no snapshot exists but version matches post.version, use live body."""
        with app.app_context():
            u = _user(_db, "b")
            p = _post(_db, u, version=1)
            result = PostVersionService.get_markdown_for_version(p.id, 1)
        assert result == "# Current\n\nCurrent body."

    def test_returns_none_for_missing_old_version(self, app, db_session):
        """Version 1 with no snapshot and post is on v2 → None."""
        with app.app_context():
            u = _user(_db, "c")
            p = _post(_db, u, version=2)
            result = PostVersionService.get_markdown_for_version(p.id, 1)
        assert result is None

    def test_returns_none_for_unknown_post(self, app, db_session):
        with app.app_context():
            result = PostVersionService.get_markdown_for_version(9999, 1)
        assert result is None

    def test_snapshot_preferred_over_live_content(self, app, db_session):
        """Snapshot body wins even when it matches the current version."""
        with app.app_context():
            u = _user(_db, "d")
            p = _post(_db, u, version=2)
            _snapshot(_db, p, 2, "# Snapshot body")
            result = PostVersionService.get_markdown_for_version(p.id, 2)
        assert result == "# Snapshot body"


class TestGetAvailableVersions:
    def test_returns_sorted_version_numbers(self, app, db_session):
        with app.app_context():
            u = _user(_db, "e")
            p = _post(_db, u, version=4)
            _snapshot(_db, p, 3, "v3")
            _snapshot(_db, p, 2, "v2")
            result = PostVersionService.get_available_versions(p.id)
        assert result == [2, 3]

    def test_empty_for_post_with_no_snapshots(self, app, db_session):
        with app.app_context():
            u = _user(_db, "f")
            p = _post(_db, u, version=1)
            result = PostVersionService.get_available_versions(p.id)
        assert result == []
