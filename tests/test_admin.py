"""Comprehensive tests for the Admin Control Center.

Coverage:
  - Access control (unauthenticated, wrong role, admin-only endpoints)
  - Dashboard snapshot
  - Post management (list, detail, set-status, toggle-feature, delete)
  - Revision moderation (list, accept, reject)
  - Comment moderation (list, hide, unflag)
  - Topics/tags (list, create, edit, delete)
  - User management (list, detail, suspend, reactivate, verify-email, set-role, shadow-ban)
  - Analytics overview
  - Site settings (GET, POST)
  - Audit log
  - System health
  - Service unit tests (AuditLogService, SiteSettingsService, AdminTagService,
    AdminUserService, ModerationService)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.extensions import db as _db
from backend.models.admin import AuditLog, SiteSetting
from backend.models.comment import Comment
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.tag import Tag
from backend.models.user import User, UserRole
from backend.services.admin_settings_service import SiteSettingsService
from backend.services.admin_tag_service import AdminTagError, AdminTagService
from backend.services.admin_user_service import AdminUserError, AdminUserService
from backend.services.audit_log_service import AuditLogService
from backend.services.moderation_service import ModerationError, ModerationService

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _login(client, user: User) -> None:
    """Set a valid Flask session for *user* on *client*."""
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


def _make_admin(make_user_token) -> tuple[User, str]:
    return make_user_token(role="admin")


def _make_editor(make_user_token) -> tuple[User, str]:
    return make_user_token(role="editor")


def _make_contributor(make_user_token) -> tuple[User, str]:
    return make_user_token(role="contributor")


def _make_reader(make_user_token) -> tuple[User, str]:
    return make_user_token(role="reader")


def _create_post(
    author: User, *, title: str = "Test Post", status: PostStatus = PostStatus.published
) -> Post:
    post = Post(
        title=title,
        slug=title.lower().replace(" ", "-"),
        markdown_body="Hello world",
        author_id=author.id,
        status=status,
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _create_revision(
    post: Post, author: User, *, status: RevisionStatus = RevisionStatus.pending
) -> Revision:
    rev = Revision(
        post_id=post.id,
        author_id=author.id,
        proposed_markdown="Updated content",
        summary="Test change",
        base_version_number=1,
        status=status,
    )
    _db.session.add(rev)
    _db.session.commit()
    return rev


def _create_comment(post: Post, author: User, *, flagged: bool = False) -> Comment:
    c = Comment(
        post_id=post.id,
        author_id=author.id,
        body="A comment",
        is_flagged=flagged,
    )
    _db.session.add(c)
    _db.session.commit()
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Access-control tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAccessControl:
    def test_unauthenticated_redirects_to_login(self, auth_client):
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code in (301, 302)
        assert "/auth/login" in resp.headers["Location"]

    def test_reader_blocked(self, auth_client, make_user_token):
        user, _ = _make_reader(make_user_token)
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        # Readers get redirected to login (not an admin role)
        assert resp.status_code in (301, 302)

    def test_contributor_blocked(self, auth_client, make_user_token):
        user, _ = _make_contributor(make_user_token)
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code in (301, 302)

    def test_editor_can_access_dashboard(self, auth_client, make_user_token):
        user, _ = _make_editor(make_user_token)
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200

    def test_admin_can_access_dashboard(self, auth_client, make_user_token):
        user, _ = _make_admin(make_user_token)
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200

    def test_editor_blocked_from_admin_only_delete(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        admin, _ = _make_admin(make_user_token)
        post = _create_post(admin)
        _login(auth_client, editor)
        resp = auth_client.post(f"/admin/posts/{post.id}/delete")
        # admin-only route → 403
        assert resp.status_code == 403

    def test_editor_blocked_from_settings(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        _login(auth_client, editor)
        resp = auth_client.get("/admin/settings")
        assert resp.status_code == 403

    def test_editor_blocked_from_system(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        _login(auth_client, editor)
        resp = auth_client.get("/admin/system")
        assert resp.status_code == 403

    def test_editor_blocked_from_topic_delete(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        tag = Tag(name="TestTag", slug="testtag")
        _db.session.add(tag)
        _db.session.commit()
        _login(auth_client, editor)
        resp = auth_client.post("/admin/topics/testtag/delete")
        assert resp.status_code == 403

    def test_editor_blocked_from_user_role_change(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        target, _ = _make_reader(make_user_token)
        _login(auth_client, editor)
        resp = auth_client.post(
            f"/admin/users/{target.id}/role", data={"role": "editor"}
        )
        assert resp.status_code == 403

    def test_index_redirects_to_dashboard(self, auth_client, make_user_token):
        user, _ = _make_admin(make_user_token)
        _login(auth_client, user)
        resp = auth_client.get("/admin/")
        assert resp.status_code in (301, 302)
        assert "dashboard" in resp.headers["Location"]


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminDashboard:
    def test_dashboard_renders(self, auth_client, make_user_token):
        user, _ = _make_admin(make_user_token)
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200
        assert b"dashboard" in resp.data.lower()

    def test_dashboard_shows_post_counts(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _create_post(
            admin, title="Dashboard Published Post", status=PostStatus.published
        )
        _create_post(admin, title="Dashboard Draft Post", status=PostStatus.draft)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200
        assert b"1" in resp.data  # at least one published


# ─────────────────────────────────────────────────────────────────────────────
# Posts
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminPosts:
    def test_posts_list_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/posts")
        assert resp.status_code == 200

    def test_posts_list_shows_post(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _create_post(admin, title="Unique Title XYZ")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/posts")
        assert b"Unique Title XYZ" in resp.data

    def test_posts_list_search(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _create_post(admin, title="Apple Post")
        _create_post(admin, title="Banana Post")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/posts?q=Apple")
        assert resp.status_code == 200
        assert b"Apple" in resp.data

    def test_post_detail_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        post = _create_post(admin)
        _login(auth_client, admin)
        resp = auth_client.get(f"/admin/posts/{post.id}")
        assert resp.status_code == 200
        assert post.title.encode() in resp.data

    def test_post_detail_404_for_missing(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/posts/99999")
        assert resp.status_code == 404

    def test_post_set_status_to_draft(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        post = _create_post(admin, status=PostStatus.published)
        _login(auth_client, admin)
        resp = auth_client.post(
            f"/admin/posts/{post.id}/status",
            data={"status": "draft"},
        )
        assert resp.status_code in (301, 302)
        _db.session.refresh(post)
        assert post.status == PostStatus.draft

    def test_post_set_status_invalid(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        post = _create_post(admin)
        _login(auth_client, admin)
        resp = auth_client.post(
            f"/admin/posts/{post.id}/status", data={"status": "nonsense"}
        )
        assert resp.status_code in (301, 302)
        # Original status unchanged
        _db.session.refresh(post)
        assert post.status == PostStatus.published

    def test_post_toggle_feature(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        post = _create_post(admin)
        assert not post.is_featured
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/posts/{post.id}/feature")
        assert resp.status_code in (301, 302)
        _db.session.refresh(post)
        assert post.is_featured

    def test_post_delete_by_admin(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        post = _create_post(admin)
        post_id = post.id
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/posts/{post_id}/delete")
        assert resp.status_code in (301, 302)
        # Post should be gone
        remaining = _db.session.get(Post, post_id)
        assert remaining is None

    def test_editor_cannot_delete_post(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        admin, _ = _make_admin(make_user_token)
        post = _create_post(admin)
        _login(auth_client, editor)
        resp = auth_client.post(f"/admin/posts/{post.id}/delete")
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Revisions
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminRevisions:
    def test_revisions_list_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/revisions")
        assert resp.status_code == 200

    def test_revisions_list_shows_pending(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        _create_revision(post, contrib)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/revisions?status=pending")
        assert resp.status_code == 200

    def test_revision_detail_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        rev = _create_revision(post, contrib)
        _login(auth_client, admin)
        resp = auth_client.get(f"/admin/revisions/{rev.id}")
        assert resp.status_code == 200

    def test_revision_detail_404(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/revisions/99999")
        assert resp.status_code == 404

    def test_revision_accept(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        rev = _create_revision(post, contrib)
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/revisions/{rev.id}/accept")
        assert resp.status_code in (301, 302)
        _db.session.refresh(rev)
        assert rev.status == RevisionStatus.accepted

    def test_revision_reject(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        rev = _create_revision(post, contrib)
        _login(auth_client, admin)
        resp = auth_client.post(
            f"/admin/revisions/{rev.id}/reject", data={"note": "Not relevant"}
        )
        assert resp.status_code in (301, 302)
        _db.session.refresh(rev)
        assert rev.status == RevisionStatus.rejected


# ─────────────────────────────────────────────────────────────────────────────
# Comments (moderation)
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminComments:
    def test_comments_list_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/comments")
        assert resp.status_code == 200

    def test_flagged_filter(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        reader, _ = _make_reader(make_user_token)
        post = _create_post(admin)
        _create_comment(post, reader, flagged=True)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/comments?flagged=1")
        assert resp.status_code == 200

    def test_comment_hide(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        reader, _ = _make_reader(make_user_token)
        post = _create_post(admin)
        comment = _create_comment(post, reader)
        assert not comment.is_deleted
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/comments/{comment.id}/hide")
        assert resp.status_code in (301, 302)
        _db.session.refresh(comment)
        assert comment.is_deleted

    def test_comment_unflag(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        reader, _ = _make_reader(make_user_token)
        post = _create_post(admin)
        comment = _create_comment(post, reader, flagged=True)
        assert comment.is_flagged
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/comments/{comment.id}/unflag")
        assert resp.status_code in (301, 302)
        _db.session.refresh(comment)
        assert not comment.is_flagged


# ─────────────────────────────────────────────────────────────────────────────
# Topics
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminTopics:
    def test_topics_list_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/topics")
        assert resp.status_code == 200

    def test_topic_create(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.post("/admin/topics/create", data={"name": "NewTopic"})
        assert resp.status_code in (301, 302)
        tag = _db.session.scalar(select(Tag).where(Tag.slug == "newtopic"))
        assert tag is not None
        assert tag.name == "NewTopic"

    def test_topic_create_duplicate_slug_flashes_error(
        self, auth_client, make_user_token
    ):
        admin, _ = _make_admin(make_user_token)
        tag = Tag(name="Existing", slug="existing")
        _db.session.add(tag)
        _db.session.commit()
        _login(auth_client, admin)
        resp = auth_client.post("/admin/topics/create", data={"name": "Existing"})
        assert resp.status_code in (301, 302)
        # Should only be one tag with this slug
        count = _db.session.scalar(
            select(_db.func.count(Tag.id)).where(Tag.slug == "existing")
        )
        assert count == 1

    def test_topic_edit(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        tag = Tag(name="OldName", slug="oldname")
        _db.session.add(tag)
        _db.session.commit()
        _login(auth_client, admin)
        resp = auth_client.post(
            "/admin/topics/oldname/edit",
            data={"name": "NewName", "description": "", "color": ""},
        )
        assert resp.status_code in (301, 302)
        _db.session.refresh(tag)
        assert tag.name == "NewName"

    def test_topic_delete(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        tag = Tag(name="ToDelete", slug="to-delete")
        _db.session.add(tag)
        _db.session.commit()
        tag_id = tag.id
        _login(auth_client, admin)
        resp = auth_client.post("/admin/topics/to-delete/delete")
        assert resp.status_code in (301, 302)
        assert _db.session.get(Tag, tag_id) is None

    def test_editor_cannot_delete_topic(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        tag = Tag(name="Protected", slug="protected")
        _db.session.add(tag)
        _db.session.commit()
        _login(auth_client, editor)
        resp = auth_client.post("/admin/topics/protected/delete")
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminUsers:
    def test_users_list_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/users")
        assert resp.status_code == 200

    def test_user_detail_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        reader, _ = _make_reader(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get(f"/admin/users/{reader.id}")
        assert resp.status_code == 200
        assert reader.username.encode() in resp.data

    def test_user_detail_404(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/users/99999")
        assert resp.status_code == 404

    def test_user_suspend(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        assert target.is_active
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/users/{target.id}/suspend")
        assert resp.status_code in (301, 302)
        _db.session.refresh(target)
        assert not target.is_active

    def test_user_reactivate(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        target.is_active = False
        _db.session.commit()
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/users/{target.id}/reactivate")
        assert resp.status_code in (301, 302)
        _db.session.refresh(target)
        assert target.is_active

    def test_user_verify_email(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        target.is_email_verified = False
        _db.session.commit()
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/users/{target.id}/verify")
        assert resp.status_code in (301, 302)
        _db.session.refresh(target)
        assert target.is_email_verified

    def test_user_set_role_by_admin(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.post(
            f"/admin/users/{target.id}/role", data={"role": "contributor"}
        )
        assert resp.status_code in (301, 302)
        _db.session.refresh(target)
        assert target.role == UserRole.contributor

    def test_user_set_role_invalid(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.post(
            f"/admin/users/{target.id}/role", data={"role": "supervillain"}
        )
        assert resp.status_code in (301, 302)
        _db.session.refresh(target)
        assert target.role == UserRole.reader  # unchanged

    def test_user_shadowban_toggle(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        assert not target.is_shadow_banned
        _login(auth_client, admin)
        resp = auth_client.post(f"/admin/users/{target.id}/shadowban")
        assert resp.status_code in (301, 302)
        _db.session.refresh(target)
        assert target.is_shadow_banned

    def test_editor_blocked_from_users(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        _login(auth_client, editor)
        resp = auth_client.get("/admin/users")
        # editors don't have manage_users capability → redirect or 403
        assert resp.status_code in (301, 302, 403)


# ─────────────────────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminAnalytics:
    def test_analytics_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code == 200

    def test_analytics_with_days_param(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics?days=14")
        assert resp.status_code == 200

    def test_analytics_days_clamped(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        # days > 90 should be clamped
        resp = auth_client.get("/admin/analytics?days=9999")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminSettings:
    def test_settings_get_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/settings")
        assert resp.status_code == 200

    def test_settings_post_updates_value(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        # First seed defaults so we have something to update
        SiteSettingsService.seed_defaults()
        _db.session.commit()
        resp = auth_client.post("/admin/settings", data={"site_title": "My Blog"})
        assert resp.status_code in (301, 302)
        assert SiteSettingsService.get("site_title") == "My Blog"

    def test_editor_blocked_from_settings(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        _login(auth_client, editor)
        resp = auth_client.get("/admin/settings")
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Audit log
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminAudit:
    def test_audit_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/audit")
        assert resp.status_code == 200

    def test_audit_log_entries_appear(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        AuditLogService.log(actor=admin, action="test.action", note="hello")
        _db.session.commit()
        _login(auth_client, admin)
        resp = auth_client.get("/admin/audit")
        assert resp.status_code == 200
        assert b"test.action" in resp.data


# ─────────────────────────────────────────────────────────────────────────────
# System health
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminSystem:
    def test_system_renders(self, auth_client, make_user_token):
        admin, _ = _make_admin(make_user_token)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/system")
        assert resp.status_code == 200
        assert b"System" in resp.data

    def test_editor_blocked_from_system(self, auth_client, make_user_token):
        editor, _ = _make_editor(make_user_token)
        _login(auth_client, editor)
        resp = auth_client.get("/admin/system")
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Service unit tests — AuditLogService
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditLogService:
    def test_log_creates_entry(self, db_session, make_user_token):
        actor, _ = _make_admin(make_user_token)
        AuditLogService.log(
            actor=actor,
            action="post.published",
            target_type="post",
            target_id=42,
            target_repr="My Post",
            note="Test note",
        )
        db_session.commit()
        entry = db_session.scalar(
            select(AuditLog).where(AuditLog.action == "post.published")
        )
        assert entry is not None
        assert entry.actor_id == actor.id
        assert entry.target_id == 42
        assert entry.note == "Test note"

    def test_log_works_without_actor(self, db_session, make_user_token):
        AuditLogService.log(actor=None, action="system.startup")
        db_session.commit()
        entry = db_session.scalar(
            select(AuditLog).where(AuditLog.action == "system.startup")
        )
        assert entry is not None
        assert entry.actor_id is None

    def test_list_entries_empty(self, db_session, make_user_token):
        entries, total = AuditLogService.list_entries()
        assert isinstance(entries, list)
        assert total == 0

    def test_list_entries_filter_by_action(self, db_session, make_user_token):
        actor, _ = _make_admin(make_user_token)
        AuditLogService.log(actor=actor, action="user.suspended")
        AuditLogService.log(actor=actor, action="post.published")
        db_session.commit()
        entries, total = AuditLogService.list_entries(action_prefix="user")
        assert total == 1
        assert entries[0].action == "user.suspended"

    def test_list_entries_filter_by_target_type(self, db_session, make_user_token):
        actor, _ = _make_admin(make_user_token)
        AuditLogService.log(actor=actor, action="x", target_type="post")
        AuditLogService.log(actor=actor, action="y", target_type="user")
        db_session.commit()
        entries, total = AuditLogService.list_entries(target_type="post")
        assert total == 1
        assert entries[0].target_type == "post"

    def test_list_entries_filter_by_actor(self, db_session, make_user_token):
        actor1, _ = _make_admin(make_user_token)
        actor2, _ = _make_editor(make_user_token)
        AuditLogService.log(actor=actor1, action="a")
        AuditLogService.log(actor=actor2, action="b")
        db_session.commit()
        entries, total = AuditLogService.list_entries(actor_id=actor1.id)
        assert total == 1
        assert entries[0].actor_id == actor1.id


# ─────────────────────────────────────────────────────────────────────────────
# Service unit tests — SiteSettingsService
# ─────────────────────────────────────────────────────────────────────────────


class TestSiteSettingsService:
    def test_seed_defaults_creates_rows(self, db_session, make_user_token):
        SiteSettingsService.seed_defaults()
        db_session.commit()
        rows = db_session.scalars(select(SiteSetting)).all()
        assert len(rows) > 0

    def test_seed_defaults_idempotent(self, db_session, make_user_token):
        SiteSettingsService.seed_defaults()
        db_session.commit()
        count1 = db_session.scalar(select(_db.func.count(SiteSetting.id)))
        SiteSettingsService.seed_defaults()
        db_session.commit()
        count2 = db_session.scalar(select(_db.func.count(SiteSetting.id)))
        assert count1 == count2

    def test_get_missing_key_returns_none(self, db_session, make_user_token):
        result = SiteSettingsService.get("nonexistent_key")
        assert result is None

    def test_set_and_get(self, db_session, make_user_token):
        SiteSettingsService.seed_defaults()
        db_session.commit()
        SiteSettingsService.set("site_name", "Hello Blog", actor=None)
        db_session.commit()
        assert SiteSettingsService.get("site_name") == "Hello Blog"

    def test_get_all_returns_dict(self, db_session, make_user_token):
        SiteSettingsService.seed_defaults()
        db_session.commit()
        result = SiteSettingsService.get_all()
        assert isinstance(result, dict)
        assert "site_title" in result

    def test_get_all_rows_returns_list(self, db_session, make_user_token):
        SiteSettingsService.seed_defaults()
        db_session.commit()
        rows = SiteSettingsService.get_all_rows()
        assert isinstance(rows, list)
        assert all(hasattr(r, "key") for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# Service unit tests — AdminTagService
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminTagService:
    def test_create_tag(self, db_session, make_user_token):
        tag = AdminTagService.create(name="Python")
        db_session.commit()
        assert tag.slug == "python"
        assert tag.name == "Python"

    def test_create_duplicate_raises(self, db_session, make_user_token):
        AdminTagService.create(name="Scala")
        db_session.commit()
        with pytest.raises(AdminTagError):
            AdminTagService.create(name="Scala")

    def test_create_empty_name_raises(self, db_session, make_user_token):
        with pytest.raises(AdminTagError):
            AdminTagService.create(name="   ")

    def test_update_tag(self, db_session, make_user_token):
        tag = AdminTagService.create(name="Go")
        db_session.commit()
        AdminTagService.update(
            tag, name="GoLang", description="Go programming language"
        )
        db_session.commit()
        assert tag.name == "GoLang"
        assert tag.description == "Go programming language"

    def test_delete_tag(self, db_session, make_user_token):
        tag = AdminTagService.create(name="Rust")
        db_session.commit()
        tag_id = tag.id
        AdminTagService.delete(tag)
        db_session.commit()
        assert db_session.get(Tag, tag_id) is None

    def test_list_tags_empty(self, db_session, make_user_token):
        items, total = AdminTagService.list_tags()
        assert items == []
        assert total == 0

    def test_list_tags_with_results(self, db_session, make_user_token):
        AdminTagService.create(name="Django")
        AdminTagService.create(name="Flask")
        db_session.commit()
        items, total = AdminTagService.list_tags()
        assert total == 2
        assert len(items) == 2

    def test_list_tags_search(self, db_session, make_user_token):
        AdminTagService.create(name="Django")
        AdminTagService.create(name="Ruby")
        db_session.commit()
        items, total = AdminTagService.list_tags(q="Dj")
        assert total == 1
        assert items[0]["tag"].name == "Django"

    def test_get_by_slug(self, db_session, make_user_token):
        AdminTagService.create(name="Haskell")
        db_session.commit()
        tag = AdminTagService.get_by_slug("haskell")
        assert tag is not None
        assert tag.name == "Haskell"

    def test_get_by_slug_missing(self, db_session, make_user_token):
        assert AdminTagService.get_by_slug("nope") is None


# ─────────────────────────────────────────────────────────────────────────────
# Service unit tests — AdminUserService
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminUserService:
    def test_get_user_detail_returns_structure(self, db_session, make_user_token):
        user, _ = _make_reader(make_user_token)
        detail = AdminUserService.get_user_detail(user.id)
        assert detail is not None
        assert "user" in detail
        assert "revision_counts" in detail
        assert "recent_revisions" in detail

    def test_get_user_detail_missing_returns_none(self, db_session, make_user_token):
        assert AdminUserService.get_user_detail(99999) is None

    def test_set_active_false(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        AdminUserService.set_active(target, False, admin)
        db_session.commit()
        assert not target.is_active

    def test_set_active_cannot_suspend_self(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        with pytest.raises(AdminUserError, match="suspend"):
            AdminUserService.set_active(admin, False, admin)

    def test_set_role(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        AdminUserService.set_role(target, UserRole.contributor, admin)
        db_session.commit()
        assert target.role == UserRole.contributor

    def test_set_role_cannot_demote_self(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        with pytest.raises(AdminUserError, match="own"):
            AdminUserService.set_role(admin, UserRole.reader, admin)

    def test_verify_email(self, db_session, make_user_token):
        target, _ = _make_reader(make_user_token)
        target.is_email_verified = False
        db_session.commit()
        AdminUserService.verify_email(target)
        db_session.commit()
        assert target.is_email_verified

    def test_set_shadow_ban(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        target, _ = _make_reader(make_user_token)
        AdminUserService.set_shadow_ban(target, True, admin)
        db_session.commit()
        assert target.is_shadow_banned

    def test_list_users_returns_all(self, db_session, make_user_token):
        _make_reader(make_user_token)
        _make_reader(make_user_token)
        users, total = AdminUserService.list_users()
        assert total >= 2

    def test_list_users_filter_by_role(self, db_session, make_user_token):
        _make_admin(make_user_token)
        _make_reader(make_user_token)
        _, total = AdminUserService.list_users(role="admin")
        assert total >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Service unit tests — ModerationService
# ─────────────────────────────────────────────────────────────────────────────


class TestModerationService:
    def test_hide_comment(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        reader, _ = _make_reader(make_user_token)
        post = _create_post(admin)
        comment = _create_comment(post, reader)
        ModerationService.hide_comment(comment.id)
        db_session.commit()
        db_session.refresh(comment)
        assert comment.is_deleted

    def test_hide_missing_comment_raises(self, db_session, make_user_token):
        with pytest.raises(ModerationError):
            ModerationService.hide_comment(99999)

    def test_unflag_comment(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        reader, _ = _make_reader(make_user_token)
        post = _create_post(admin)
        comment = _create_comment(post, reader, flagged=True)
        ModerationService.unflag_comment(comment.id)
        db_session.commit()
        db_session.refresh(comment)
        assert not comment.is_flagged

    def test_list_comments_empty(self, db_session, make_user_token):
        items, total = ModerationService.list_comments()
        assert items == []
        assert total == 0

    def test_list_comments_flagged_only(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        reader, _ = _make_reader(make_user_token)
        post = _create_post(admin)
        _create_comment(post, reader, flagged=False)
        _create_comment(post, reader, flagged=True)
        items, total = ModerationService.list_comments(flagged_only=True)
        assert total == 1
        assert items[0].is_flagged

    def test_accept_revision(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        rev = _create_revision(post, contrib)
        ModerationService.accept_revision(rev.id, admin)
        db_session.commit()
        db_session.refresh(rev)
        assert rev.status == RevisionStatus.accepted

    def test_reject_revision(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        rev = _create_revision(post, contrib)
        ModerationService.reject_revision(rev.id, admin, note="Off topic")
        db_session.commit()
        db_session.refresh(rev)
        assert rev.status == RevisionStatus.rejected

    def test_cannot_accept_already_accepted(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        rev = _create_revision(post, contrib, status=RevisionStatus.accepted)
        with pytest.raises(ModerationError):
            ModerationService.accept_revision(rev.id, admin)

    def test_list_revisions_by_status(self, db_session, make_user_token):
        admin, _ = _make_admin(make_user_token)
        contrib, _ = _make_contributor(make_user_token)
        post = _create_post(admin)
        _create_revision(post, contrib, status=RevisionStatus.pending)
        _create_revision(post, contrib, status=RevisionStatus.accepted)
        items, total = ModerationService.list_revisions(status="pending")
        assert total == 1
        assert items[0].status == RevisionStatus.pending
