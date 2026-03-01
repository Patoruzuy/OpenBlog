"""Tests for the analytics API endpoints."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.services.analytics_service import AnalyticsService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    user, tok = make_user_token(
        "analytics_api_author@example.com", "analyticsapiauthor"
    )
    return user, tok


@pytest.fixture()
def editor(make_user_token):
    user, tok = make_user_token(
        "analytics_api_editor@example.com", "analyticsapieditor", role="editor"
    )
    return user, tok


@pytest.fixture()
def admin(make_user_token):
    user, tok = make_user_token(
        "analytics_api_admin@example.com", "analyticsapiadmin", role="admin"
    )
    return user, tok


@pytest.fixture()
def reader(make_user_token):
    user, tok = make_user_token(
        "analytics_api_reader@example.com", "analyticsapireader"
    )
    return user, tok


@pytest.fixture()
def pub_post(author, db_session):
    user, _ = author
    post = Post(
        author_id=user.id,
        slug="analytics-api-post",
        title="Analytics API Post",
        markdown_body="# Analytics",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── GET /api/posts/<slug>/analytics ──────────────────────────────────────────


class TestPostAnalytics:
    def test_author_can_access_own_post(
        self, auth_client, author, pub_post, db_session
    ):
        _, tok = author
        resp = auth_client.get(f"/api/posts/{pub_post.slug}/analytics", headers=_h(tok))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["post_id"] == pub_post.id
        assert "views" in data
        assert "unique_sessions" in data
        assert "total_events" in data
        assert "views_last_30_days" in data
        assert "top_referrers" in data

    def test_editor_can_access_any_post(
        self, auth_client, editor, pub_post, db_session
    ):
        _, tok = editor
        resp = auth_client.get(f"/api/posts/{pub_post.slug}/analytics", headers=_h(tok))
        assert resp.status_code == 200

    def test_admin_can_access_any_post(self, auth_client, admin, pub_post, db_session):
        _, tok = admin
        resp = auth_client.get(f"/api/posts/{pub_post.slug}/analytics", headers=_h(tok))
        assert resp.status_code == 200

    def test_reader_cannot_access_others_post(
        self, auth_client, reader, pub_post, db_session
    ):
        _, tok = reader
        resp = auth_client.get(f"/api/posts/{pub_post.slug}/analytics", headers=_h(tok))
        assert resp.status_code == 403

    def test_unauthenticated_returns_401(self, auth_client, pub_post, db_session):
        resp = auth_client.get(f"/api/posts/{pub_post.slug}/analytics")
        assert resp.status_code == 401

    def test_unknown_post_returns_404(self, auth_client, editor, db_session):
        _, tok = editor
        resp = auth_client.get("/api/posts/no-such-post/analytics", headers=_h(tok))
        assert resp.status_code == 404

    def test_reflects_recorded_events(self, auth_client, author, pub_post, db_session):
        _, tok = author
        AnalyticsService.record_event("post_view", post_id=pub_post.id)
        AnalyticsService.record_event("post_view", post_id=pub_post.id)

        resp = auth_client.get(f"/api/posts/{pub_post.slug}/analytics", headers=_h(tok))
        data = resp.get_json()
        assert data["views"] == 2

    def test_slug_in_response(self, auth_client, author, pub_post, db_session):
        _, tok = author
        resp = auth_client.get(f"/api/posts/{pub_post.slug}/analytics", headers=_h(tok))
        assert resp.get_json()["slug"] == pub_post.slug


# ── GET /api/analytics/top-posts ─────────────────────────────────────────────


class TestTopPosts:
    def test_editor_can_access(self, auth_client, editor, pub_post, db_session):
        _, tok = editor
        resp = auth_client.get("/api/analytics/top-posts", headers=_h(tok))
        assert resp.status_code == 200

    def test_admin_can_access(self, auth_client, admin, pub_post, db_session):
        _, tok = admin
        resp = auth_client.get("/api/analytics/top-posts", headers=_h(tok))
        assert resp.status_code == 200

    def test_reader_gets_403(self, auth_client, reader, db_session):
        _, tok = reader
        resp = auth_client.get("/api/analytics/top-posts", headers=_h(tok))
        assert resp.status_code == 403

    def test_unauthenticated_gets_401(self, auth_client, db_session):
        resp = auth_client.get("/api/analytics/top-posts")
        assert resp.status_code == 401

    def test_response_shape(self, auth_client, editor, pub_post, db_session):
        _, tok = editor
        AnalyticsService.record_event("post_view", post_id=pub_post.id)
        resp = auth_client.get("/api/analytics/top-posts", headers=_h(tok))
        data = resp.get_json()
        assert "items" in data
        assert "limit" in data
        assert "days" in data

    def test_limit_param(self, auth_client, editor, db_session):
        _, tok = editor
        resp = auth_client.get("/api/analytics/top-posts?limit=5", headers=_h(tok))
        assert resp.status_code == 200
        assert resp.get_json()["limit"] == 5

    def test_days_param(self, auth_client, editor, db_session):
        _, tok = editor
        resp = auth_client.get("/api/analytics/top-posts?days=7", headers=_h(tok))
        assert resp.status_code == 200
        assert resp.get_json()["days"] == 7

    def test_limit_capped_at_50(self, auth_client, editor, db_session):
        _, tok = editor
        resp = auth_client.get("/api/analytics/top-posts?limit=9999", headers=_h(tok))
        assert resp.get_json()["limit"] == 50

    def test_days_capped_at_365(self, auth_client, editor, db_session):
        _, tok = editor
        resp = auth_client.get("/api/analytics/top-posts?days=9999", headers=_h(tok))
        assert resp.get_json()["days"] == 365

    def test_reflects_recorded_events(self, auth_client, editor, pub_post, db_session):
        _, tok = editor
        AnalyticsService.record_event("post_view", post_id=pub_post.id)
        AnalyticsService.record_event("post_view", post_id=pub_post.id)

        resp = auth_client.get("/api/analytics/top-posts", headers=_h(tok))
        items = resp.get_json()["items"]
        assert len(items) >= 1
        top = next(i for i in items if i["post_id"] == pub_post.id)
        assert top["view_count"] == 2
        assert top["slug"] == pub_post.slug
