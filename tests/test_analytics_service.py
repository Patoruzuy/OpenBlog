"""Tests for AnalyticsService."""

from __future__ import annotations

import json

import pytest

from backend.extensions import db
from backend.models.analytics import AnalyticsEvent
from backend.models.post import Post, PostStatus
from backend.services.analytics_service import AnalyticsService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    user, _ = make_user_token("analytics_author@example.com", "analyticsauthor")
    return user


@pytest.fixture()
def pub_post(author, db_session):
    post = Post(
        author_id=author.id,
        slug="analytics-target-post",
        title="Analytics Target Post",
        markdown_body="# Hello",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── record_event ──────────────────────────────────────────────────────────────


class TestRecordEvent:
    def test_creates_event_row(self, pub_post, db_session):
        event = AnalyticsService.record_event("post_view", post_id=pub_post.id)
        assert event.id is not None
        assert event.event_type == "post_view"
        assert event.post_id == pub_post.id

    def test_hashes_user_agent(self, pub_post, db_session):
        event = AnalyticsService.record_event(
            "post_view",
            post_id=pub_post.id,
            user_agent="Mozilla/5.0",
        )
        assert event.user_agent_hash is not None
        assert len(event.user_agent_hash) == 16
        # Raw UA is NOT stored
        assert "Mozilla" not in (event.user_agent_hash or "")

    def test_truncates_long_referrer(self, pub_post, db_session):
        long_ref = "https://example.com/" + "x" * 600
        event = AnalyticsService.record_event(
            "post_view",
            post_id=pub_post.id,
            referrer=long_ref,
        )
        assert len(event.referrer) == 512

    def test_nullable_fields_accepted(self, db_session):
        event = AnalyticsService.record_event("search")
        assert event.post_id is None
        assert event.user_id is None
        assert event.session_id is None

    def test_different_event_types(self, pub_post, db_session):
        for etype in ("post_view", "search", "page_view"):
            AnalyticsService.record_event(etype, post_id=pub_post.id)

        from sqlalchemy import select

        events = list(
            db.session.scalars(
                select(AnalyticsEvent).where(AnalyticsEvent.post_id == pub_post.id)
            )
        )
        assert len(events) == 3


# ── queue_event ───────────────────────────────────────────────────────────────


class TestQueueEvent:
    def test_pushes_to_redis_list(self, app, pub_post, db_session):
        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")  # start clean

            AnalyticsService.queue_event("post_view", post_id=pub_post.id)
            assert redis.llen("analytics:event_queue") == 1

    def test_queued_payload_is_valid_json(self, app, pub_post, db_session):
        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")

            AnalyticsService.queue_event(
                "post_view",
                post_id=pub_post.id,
                user_id=42,
                session_id="sess-abc",
            )
            items = redis.lrange("analytics:event_queue", 0, -1)
            payload = json.loads(items[0])
            assert payload["event_type"] == "post_view"
            assert payload["post_id"] == pub_post.id
            assert payload["user_id"] == 42
            assert payload["session_id"] == "sess-abc"
            assert "occurred_at" in payload

    def test_hashes_ua_in_queue(self, app, pub_post, db_session):
        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")

            AnalyticsService.queue_event(
                "post_view",
                post_id=pub_post.id,
                user_agent="TestBrowser/1.0",
            )
            items = redis.lrange("analytics:event_queue", 0, -1)
            payload = json.loads(items[0])
            assert payload.get("user_agent_hash") is not None
            assert "TestBrowser" not in str(payload)


# ── flush_queued_events ───────────────────────────────────────────────────────


class TestFlushQueuedEvents:
    def test_flushes_to_db(self, app, pub_post, db_session):
        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")

            AnalyticsService.queue_event("post_view", post_id=pub_post.id)
            AnalyticsService.queue_event("post_view", post_id=pub_post.id)

            count = AnalyticsService.flush_queued_events()
            assert count == 2

    def test_clears_redis_queue_after_flush(self, app, pub_post, db_session):
        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")

            AnalyticsService.queue_event("post_view", post_id=pub_post.id)
            AnalyticsService.flush_queued_events()

            assert redis.llen("analytics:event_queue") == 0

    def test_events_written_to_db(self, app, pub_post, db_session):
        from sqlalchemy import select

        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")

            AnalyticsService.queue_event(
                "post_view", post_id=pub_post.id, session_id="s1"
            )
            AnalyticsService.flush_queued_events()

            events = list(
                db.session.scalars(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.post_id == pub_post.id,
                        AnalyticsEvent.session_id == "s1",
                    )
                )
            )
            assert len(events) == 1

    def test_empty_queue_returns_zero(self, app, db_session):
        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")

            count = AnalyticsService.flush_queued_events()
            assert count == 0

    def test_ignores_malformed_json(self, app, db_session):
        with app.app_context():
            redis = app.extensions["redis"]
            redis.delete("analytics:event_queue")
            redis.rpush("analytics:event_queue", "not-valid-json")

            count = AnalyticsService.flush_queued_events()
            assert count == 0  # malformed item skipped


# ── get_post_stats ────────────────────────────────────────────────────────────


class TestGetPostStats:
    def test_returns_zero_when_no_events(self, pub_post, db_session):
        stats = AnalyticsService.get_post_stats(pub_post.id)
        assert stats["total_events"] == 0
        assert stats["views"] == 0
        assert stats["unique_sessions"] == 0

    def test_counts_views(self, pub_post, db_session):
        for i in range(5):
            AnalyticsService.record_event(
                "post_view", post_id=pub_post.id, session_id=f"s{i}"
            )
        stats = AnalyticsService.get_post_stats(pub_post.id)
        assert stats["views"] == 5

    def test_unique_sessions(self, pub_post, db_session):
        # 3 views from 2 distinct sessions
        AnalyticsService.record_event("post_view", post_id=pub_post.id, session_id="A")
        AnalyticsService.record_event("post_view", post_id=pub_post.id, session_id="A")
        AnalyticsService.record_event("post_view", post_id=pub_post.id, session_id="B")
        stats = AnalyticsService.get_post_stats(pub_post.id)
        assert stats["unique_sessions"] == 2
        assert stats["views"] == 3

    def test_top_referrers(self, pub_post, db_session):
        for _ in range(3):
            AnalyticsService.record_event(
                "post_view", post_id=pub_post.id, referrer="https://google.com"
            )
        AnalyticsService.record_event(
            "post_view", post_id=pub_post.id, referrer="https://bing.com"
        )
        stats = AnalyticsService.get_post_stats(pub_post.id)
        refs = {r["referrer"]: r["count"] for r in stats["top_referrers"]}
        assert refs["https://google.com"] == 3
        assert refs["https://bing.com"] == 1

    def test_includes_post_slug(self, pub_post, db_session):
        stats = AnalyticsService.get_post_stats(pub_post.id)
        assert stats["slug"] == pub_post.slug

    def test_post_id_in_result(self, pub_post, db_session):
        stats = AnalyticsService.get_post_stats(pub_post.id)
        assert stats["post_id"] == pub_post.id


# ── get_top_posts ─────────────────────────────────────────────────────────────


class TestGetTopPosts:
    def test_returns_empty_when_no_events(self, db_session):
        results = AnalyticsService.get_top_posts(limit=5)
        assert results == []

    def test_ranks_by_view_count(self, author, db_session):
        # Two posts with different view counts
        post_a = Post(
            author_id=author.id,
            slug="top-post-a",
            title="Top A",
            markdown_body="# A",
            status=PostStatus.published,
        )
        post_b = Post(
            author_id=author.id,
            slug="top-post-b",
            title="Top B",
            markdown_body="# B",
            status=PostStatus.published,
        )
        db.session.add_all([post_a, post_b])
        db.session.commit()

        for _ in range(5):
            AnalyticsService.record_event("post_view", post_id=post_a.id)
        for _ in range(2):
            AnalyticsService.record_event("post_view", post_id=post_b.id)

        results = AnalyticsService.get_top_posts(limit=5)
        assert results[0]["post_id"] == post_a.id
        assert results[0]["view_count"] == 5
        assert results[1]["post_id"] == post_b.id

    def test_respects_limit(self, author, db_session):
        for i in range(5):
            p = Post(
                author_id=author.id,
                slug=f"limit-post-{i}",
                title=f"Limit {i}",
                markdown_body="# x",
                status=PostStatus.published,
            )
            db.session.add(p)
            db.session.commit()
            AnalyticsService.record_event("post_view", post_id=p.id)

        results = AnalyticsService.get_top_posts(limit=3)
        assert len(results) == 3
