"""Tests for ReportService."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.services.report_service import ReportError, ReportService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@example.com", "alice")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _ = make_user_token("bob@example.com", "bob")
    return user


@pytest.fixture()
def pub_post(alice, db_session):
    from backend.extensions import db

    post = Post(
        author_id=alice.id,
        title="Reportable Post",
        slug="reportable-post",
        markdown_body="# Hello",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── submit ────────────────────────────────────────────────────────────────────


class TestSubmit:
    def test_submit_post_report_returns_report(self, bob, pub_post, db_session):
        report = ReportService.submit(
            reporter_id=bob.id,
            target_type="post",
            target_id=pub_post.id,
            reason="spam",
        )
        assert report.id is not None
        assert report.reporter_id == bob.id
        assert report.target_type == "post"
        assert report.target_id == pub_post.id
        assert report.reason == "spam"
        assert report.status == "open"

    def test_submit_sets_note(self, bob, pub_post, db_session):
        report = ReportService.submit(
            reporter_id=bob.id,
            target_type="post",
            target_id=pub_post.id,
            reason="harassment",
            note="This is offensive.",
        )
        assert report.note == "This is offensive."

    def test_invalid_target_type_raises_400(self, bob, pub_post, db_session):
        with pytest.raises(ReportError) as exc:
            ReportService.submit(
                reporter_id=bob.id,
                target_type="thing",
                target_id=pub_post.id,
                reason="spam",
            )
        assert exc.value.status_code == 400

    def test_invalid_reason_raises_400(self, bob, pub_post, db_session):
        with pytest.raises(ReportError) as exc:
            ReportService.submit(
                reporter_id=bob.id,
                target_type="post",
                target_id=pub_post.id,
                reason="not-a-real-reason",
            )
        assert exc.value.status_code == 400

    def test_empty_reason_raises_400(self, bob, pub_post, db_session):
        with pytest.raises(ReportError) as exc:
            ReportService.submit(
                reporter_id=bob.id,
                target_type="post",
                target_id=pub_post.id,
                reason="",
            )
        assert exc.value.status_code == 400

    def test_duplicate_open_report_raises_409(self, bob, pub_post, db_session):
        ReportService.submit(
            reporter_id=bob.id,
            target_type="post",
            target_id=pub_post.id,
            reason="spam",
        )
        with pytest.raises(ReportError) as exc:
            ReportService.submit(
                reporter_id=bob.id,
                target_type="post",
                target_id=pub_post.id,
                reason="spam",
            )
        assert exc.value.status_code == 409

    def test_can_re_report_after_resolve(self, bob, alice, pub_post, db_session):
        report = ReportService.submit(
            reporter_id=bob.id,
            target_type="post",
            target_id=pub_post.id,
            reason="spam",
        )
        ReportService.resolve(report.id, resolver_id=alice.id)
        # Should succeed now
        report2 = ReportService.submit(
            reporter_id=bob.id,
            target_type="post",
            target_id=pub_post.id,
            reason="spam",
        )
        assert report2.status == "open"


# ── resolve / dismiss ─────────────────────────────────────────────────────────


class TestResolveAndDismiss:
    def test_resolve_marks_resolved(self, bob, alice, pub_post, db_session):
        report = ReportService.submit(
            reporter_id=bob.id,
            target_type="post",
            target_id=pub_post.id,
            reason="spam",
        )
        resolved = ReportService.resolve(report.id, resolver_id=alice.id)
        assert resolved.status == "resolved"
        assert resolved.resolved_by_id == alice.id

    def test_dismiss_marks_dismissed(self, bob, alice, pub_post, db_session):
        report = ReportService.submit(
            reporter_id=bob.id,
            target_type="post",
            target_id=pub_post.id,
            reason="misinformation",
        )
        dismissed = ReportService.resolve(report.id, resolver_id=alice.id, dismiss=True)
        assert dismissed.status == "dismissed"

    def test_resolve_nonexistent_raises_404(self, alice, db_session):
        with pytest.raises(ReportError) as exc:
            ReportService.resolve(99999, resolver_id=alice.id)
        assert exc.value.status_code == 404
