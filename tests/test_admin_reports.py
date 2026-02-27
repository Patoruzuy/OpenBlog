"""Tests for the admin reports moderation back-office.

Coverage:
  - /admin/reports GET (list, filters, pagination)
  - /admin/reports/<id>/resolve POST
  - /admin/reports/<id>/dismiss POST
  - AuditLog entry created on resolve / dismiss
  - ReportService.list_reports and open_count helpers
  - Access control (reader/contributor blocked, editor/admin allowed)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.extensions import db as _db
from backend.models.admin import AuditLog
from backend.models.post import Post, PostStatus
from backend.models.report import Report
from backend.models.user import User, UserRole
from backend.services.report_service import ReportService


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_user(db, *, role: str = "reader") -> User:
    u = User(
        email=f"{role}_{id(db)}@test.com",
        username=f"u_{role}_{id(db)}",
        password_hash="x",
        role=UserRole(role),
    )
    db.session.add(u)
    db.session.commit()
    return u


def _make_post(db, author: User, *, n: int = 1) -> Post:
    post = Post(
        title=f"Post {n}",
        slug=f"post-{n}-{id(db)}",
        markdown_body="body",
        author_id=author.id,
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


def _make_report(
    db,
    reporter: User,
    target: Post,
    *,
    reason: str = "spam",
    status: str = "open",
) -> Report:
    r = Report(
        reporter_id=reporter.id,
        target_type="post",
        target_id=target.id,
        reason=reason,
        status=status,
    )
    db.session.add(r)
    db.session.commit()
    return r


def _login(client, user: User) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ─────────────────────────────────────────────────────────────────────────────
# ReportService unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestListReports:
    def test_empty_returns_empty_list(self, db_session):
        reports, total = ReportService.list_reports()
        assert reports == []
        assert total == 0

    def test_filters_by_status_open(self, db_session):
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author)
        _make_report(_db, reporter, post, status="open")
        _make_report(_db, reporter, post, status="resolved")

        # Second report can't be open (unique open constraint) — use a fresh reporter
        reporter2 = User(email="r2@x.com", username="r2", password_hash="x", role=UserRole.reader)
        _db.session.add(reporter2)
        _db.session.commit()
        _make_report(_db, reporter2, post, status="resolved")

        open_reports, total = ReportService.list_reports(status="open")
        assert total == 1
        assert all(r.status == "open" for r in open_reports)

    def test_filters_by_target_type(self, db_session):
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author)
        _make_report(_db, reporter, post)

        comment_report = Report(
            reporter_id=reporter.id,
            target_type="comment",
            target_id=99,
            reason="spam",
            status="open",
        )
        _db.session.add(comment_report)
        _db.session.commit()

        post_reports, total = ReportService.list_reports(target_type="post")
        assert total == 1
        assert all(r.target_type == "post" for r in post_reports)

    def test_all_status_returns_all(self, db_session):
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author)
        _make_report(_db, reporter, post, status="open")

        reporter2 = User(email="r3@x.com", username="r3", password_hash="x", role=UserRole.reader)
        _db.session.add(reporter2)
        _db.session.commit()
        _make_report(_db, reporter2, post, status="dismissed")

        _, total = ReportService.list_reports(status="all")
        assert total == 2

    def test_pagination(self, db_session):
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author)
        reporters = []
        for i in range(5):
            u = User(email=f"pg{i}@x.com", username=f"pgr{i}", password_hash="x", role=UserRole.reader)
            _db.session.add(u)
            _db.session.commit()
            reporters.append(u)
        for u in reporters:
            _make_report(_db, u, post)

        page1, total = ReportService.list_reports(page=1, per_page=3, status="all")
        assert total == 5
        assert len(page1) == 3

        page2, _ = ReportService.list_reports(page=2, per_page=3, status="all")
        assert len(page2) == 2


class TestOpenCount:
    def test_zero_when_no_reports(self, db_session):
        assert ReportService.open_count() == 0

    def test_counts_only_open(self, db_session):
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author)
        _make_report(_db, reporter, post, status="open")
        reporter2 = User(email="oc2@x.com", username="oc2", password_hash="x", role=UserRole.reader)
        _db.session.add(reporter2)
        _db.session.commit()
        _make_report(_db, reporter2, post, status="resolved")
        assert ReportService.open_count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Admin route access control
# ─────────────────────────────────────────────────────────────────────────────


class TestReportsAccessControl:
    def test_anonymous_redirects_to_login(self, client, db_session):
        resp = client.get("/admin/reports")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_reader_is_blocked(self, client, db_session):
        reader = _make_user(_db, role="reader")
        _login(client, reader)
        resp = client.get("/admin/reports")
        assert resp.status_code == 302

    def test_contributor_is_blocked(self, client, db_session):
        contrib = _make_user(_db, role="contributor")
        _login(client, contrib)
        resp = client.get("/admin/reports")
        assert resp.status_code == 302

    def test_editor_can_access(self, client, db_session):
        editor = _make_user(_db, role="editor")
        _login(client, editor)
        resp = client.get("/admin/reports")
        assert resp.status_code == 200

    def test_admin_can_access(self, client, db_session):
        admin = _make_user(_db, role="admin")
        _login(client, admin)
        resp = client.get("/admin/reports")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# GET /admin/reports — list and filters
# ─────────────────────────────────────────────────────────────────────────────


class TestReportsList:
    def test_renders_with_no_reports(self, client, db_session):
        admin = _make_user(_db, role="admin")
        _login(client, admin)
        resp = client.get("/admin/reports")
        assert resp.status_code == 200
        assert b"No reports found" in resp.data

    def test_shows_open_report_by_default(self, client, db_session):
        admin = _make_user(_db, role="admin")
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, n=10)
        _make_report(_db, reporter, post)
        _login(client, admin)
        resp = client.get("/admin/reports")
        assert resp.status_code == 200
        assert b"spam" in resp.data.lower() or b"Spam" in resp.data

    def test_status_filter_all(self, client, db_session):
        admin = _make_user(_db, role="admin")
        _login(client, admin)
        resp = client.get("/admin/reports?status=all")
        assert resp.status_code == 200

    def test_status_filter_resolved(self, client, db_session):
        admin = _make_user(_db, role="admin")
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, n=20)
        _make_report(_db, reporter, post, status="resolved")
        _login(client, admin)
        resp = client.get("/admin/reports?status=resolved")
        assert resp.status_code == 200
        assert b"resolved" in resp.data

    def test_target_type_filter(self, client, db_session):
        admin = _make_user(_db, role="admin")
        _login(client, admin)
        resp = client.get("/admin/reports?target_type=comment")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/reports/<id>/resolve
# ─────────────────────────────────────────────────────────────────────────────


class TestReportResolve:
    def test_resolve_sets_status(self, client, db_session):
        admin = _make_user(_db, role="admin")
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, n=30)
        report = _make_report(_db, reporter, post)
        _login(client, admin)
        resp = client.post(f"/admin/reports/{report.id}/resolve")
        assert resp.status_code == 302
        _db.session.refresh(report)
        assert report.status == "resolved"
        assert report.resolved_by_id == admin.id

    def test_resolve_creates_audit_log_entry(self, client, db_session):
        admin = _make_user(_db, role="admin")
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, n=31)
        report = _make_report(_db, reporter, post)
        _login(client, admin)
        client.post(f"/admin/reports/{report.id}/resolve")
        entry = _db.session.scalar(
            select(AuditLog).where(AuditLog.action == "report.resolve")
        )
        assert entry is not None
        assert entry.actor_id == admin.id
        assert entry.target_type == "post"

    def test_resolve_nonexistent_report_flashes_error(self, client, db_session):
        admin = _make_user(_db, role="admin")
        _login(client, admin)
        resp = client.post("/admin/reports/99999/resolve")
        assert resp.status_code == 302  # redirect with flash error


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/reports/<id>/dismiss
# ─────────────────────────────────────────────────────────────────────────────


class TestReportDismiss:
    def test_dismiss_sets_dismissed_status(self, client, db_session):
        admin = _make_user(_db, role="admin")
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, n=40)
        report = _make_report(_db, reporter, post)
        _login(client, admin)
        resp = client.post(f"/admin/reports/{report.id}/dismiss")
        assert resp.status_code == 302
        _db.session.refresh(report)
        assert report.status == "dismissed"
        assert report.resolved_by_id == admin.id

    def test_dismiss_creates_audit_log_entry(self, client, db_session):
        admin = _make_user(_db, role="admin")
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, n=41)
        report = _make_report(_db, reporter, post)
        _login(client, admin)
        client.post(f"/admin/reports/{report.id}/dismiss")
        entry = _db.session.scalar(
            select(AuditLog).where(AuditLog.action == "report.dismiss")
        )
        assert entry is not None
        assert entry.note == "dismissed"

    def test_editor_can_dismiss(self, client, db_session):
        editor = _make_user(_db, role="editor")
        reporter = _make_user(_db)
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, n=42)
        report = _make_report(_db, reporter, post)
        _login(client, editor)
        resp = client.post(f"/admin/reports/{report.id}/dismiss")
        assert resp.status_code == 302
        _db.session.refresh(report)
        assert report.status == "dismissed"
