"""Tests for Prometheus business-metric counters.

These tests verify that the metric singletons exist and that calling the
relevant service methods increments them by the expected amount.

Because prometheus_client uses a global REGISTRY that persists across tests,
we compare the counter value *before* and *after* each action rather than
asserting a specific absolute value.  This makes the tests order-independent.

Note: METRICS_ENABLED=False in TestingConfig, so the /metrics HTTP endpoint
is *not* registered on the default test app.  Business counters (Counter,
Histogram objects) are module-level singletons and collect data regardless.
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

# ── Helpers ───────────────────────────────────────────────────────────────────


def _val(metric_name: str, labels: dict | None = None) -> float:
    """Return the current sample value for a metric (0.0 if not yet observed)."""
    return REGISTRY.get_sample_value(metric_name, labels=labels) or 0.0


# ── Metric existence ──────────────────────────────────────────────────────────


class TestMetricDefinitions:
    """All expected metric objects are importable and have the correct type."""

    def test_all_counters_importable(self):
        from prometheus_client import Counter

        from backend.utils.metrics import (
            bookmarks_created,
            celery_tasks_total,
            comments_created,
            posts_created,
            posts_published,
            revisions_accepted,
            revisions_rejected,
            revisions_submitted,
            search_queries,
            user_logins,
            user_registrations,
        )

        for counter in (
            posts_created,
            posts_published,
            user_registrations,
            revisions_submitted,
            revisions_accepted,
            revisions_rejected,
            comments_created,
            search_queries,
            bookmarks_created,
            celery_tasks_total,
        ):
            assert isinstance(counter, Counter)
        # user_logins is a labelled counter — still a Counter
        assert isinstance(user_logins, Counter)

    def test_histograms_importable(self):
        from prometheus_client import Histogram

        from backend.utils.metrics import (
            celery_task_duration_seconds,
            db_query_duration_seconds,
        )

        assert isinstance(db_query_duration_seconds, Histogram)
        assert isinstance(celery_task_duration_seconds, Histogram)

    def test_build_info_importable(self):
        from prometheus_client import Info

        from backend.utils.metrics import build_info

        assert isinstance(build_info, Info)

    def test_metric_names_in_registry(self):
        """All metric names appear in the default REGISTRY.

        We check metric *family* names (metric.name) rather than individual
        sample names because labeled counters (e.g. user_logins) emit no
        samples until at least one label combination is observed.
        """
        # prometheus_client >= 0.12 strips the ``_total`` suffix from Counter
        # family names (and ``_info`` from Info names) when storing them in the
        # registry.  Use the bare family names so the assertion is stable across
        # versions and doesn't depend on whether labels have been observed yet.
        expected = [
            "openblog_posts_created",
            "openblog_posts_published",
            "openblog_user_registrations",
            "openblog_user_logins",  # labelled counter – no samples until inc'd
            "openblog_revisions_submitted",
            "openblog_revisions_accepted",
            "openblog_revisions_rejected",
            "openblog_comments_created",
            "openblog_search_queries",
            "openblog_bookmarks_created",
            "openblog_celery_tasks",
            "openblog_db_query_duration_seconds",
            "openblog_celery_task_duration_seconds",
        ]
        # metric.name is the family name and is always present once registered,
        # even for labeled metrics that have never been incremented.
        all_family_names = {metric.name for metric in REGISTRY.collect()}
        for name in expected:
            assert name in all_family_names, (
                f"Expected metric family '{name}' not found in registry. "
                f"Available: {sorted(n for n in all_family_names if n.startswith('openblog'))}"
            )


# ── Auth metrics ──────────────────────────────────────────────────────────────


class TestAuthMetrics:
    def test_registration_increments_counter(self, db_session):
        before = _val("openblog_user_registrations_total")
        from backend.services.auth_service import AuthService

        AuthService.register(
            "reg_metrics@example.com", "reg_metrics_user", "StrongPass123!!"
        )
        assert _val("openblog_user_registrations_total") == before + 1

    def test_successful_login_increments_success_counter(self, db_session):
        from backend.services.auth_service import AuthService

        AuthService.register("login_ok@example.com", "login_ok_user", "StrongPass123!!")
        before = _val("openblog_user_logins_total", {"outcome": "success"})
        AuthService.login("login_ok@example.com", "StrongPass123!!")
        assert _val("openblog_user_logins_total", {"outcome": "success"}) == before + 1

    def test_wrong_password_increments_failure_counter(self, db_session):
        from backend.services.auth_service import AuthError, AuthService

        AuthService.register(
            "login_fail@example.com", "login_fail_user", "StrongPass123!!"
        )
        before = _val("openblog_user_logins_total", {"outcome": "failure"})
        with pytest.raises(AuthError):
            AuthService.login("login_fail@example.com", "wrongpassword")
        assert _val("openblog_user_logins_total", {"outcome": "failure"}) == before + 1

    def test_unknown_email_increments_failure_counter(self, db_session):
        from backend.services.auth_service import AuthError, AuthService

        before = _val("openblog_user_logins_total", {"outcome": "failure"})
        with pytest.raises(AuthError):
            AuthService.login("nobody@example.com", "StrongPass123!!")
        assert _val("openblog_user_logins_total", {"outcome": "failure"}) == before + 1


# ── Post metrics ──────────────────────────────────────────────────────────────


class TestPostMetrics:
    def test_create_post_increments_counter(self, db_session, make_user_token):
        user, _ = make_user_token(role="contributor")
        before = _val("openblog_posts_created_total")
        from backend.services.post_service import PostService

        PostService.create(user.id, "Metrics Test Post")
        assert _val("openblog_posts_created_total") == before + 1

    def test_publish_post_increments_counter(self, db_session, make_user_token):
        user, _ = make_user_token(role="contributor")
        before = _val("openblog_posts_published_total")
        from backend.services.post_service import PostService

        post = PostService.create(user.id, "Publish Metrics Post")
        PostService.publish(post)
        assert _val("openblog_posts_published_total") == before + 1

    def test_scheduled_publish_does_not_increment_immediately(
        self, db_session, make_user_token
    ):
        """Scheduling a post for the future must NOT increment posts_published."""
        from datetime import UTC, datetime, timedelta

        from backend.services.post_service import PostService

        user, _ = make_user_token(role="contributor")
        before = _val("openblog_posts_published_total")
        post = PostService.create(user.id, "Scheduled Post Metrics")
        future = datetime.now(UTC) + timedelta(hours=1)
        PostService.publish(post, at=future)
        assert _val("openblog_posts_published_total") == before  # unchanged


# ── Comment metrics ───────────────────────────────────────────────────────────


class TestCommentMetrics:
    def test_create_comment_increments_counter(self, db_session, make_user_token):
        from backend.services.comment_service import CommentService
        from backend.services.post_service import PostService

        user, _ = make_user_token(role="contributor")
        post = PostService.create(user.id, "Comment Metrics Post")
        PostService.publish(post)
        before = _val("openblog_comments_created_total")
        CommentService.create(post.id, user.id, "Nice post!")
        assert _val("openblog_comments_created_total") == before + 1


# ── Search metrics ────────────────────────────────────────────────────────────


class TestSearchMetrics:
    def test_non_empty_search_increments_counter(self, db_session):
        from backend.services.search_service import SearchService

        before = _val("openblog_search_queries_total")
        SearchService.search("flask")
        assert _val("openblog_search_queries_total") == before + 1

    def test_empty_search_does_not_increment(self, db_session):
        from backend.services.search_service import SearchService

        before = _val("openblog_search_queries_total")
        SearchService.search("   ")
        assert _val("openblog_search_queries_total") == before  # unchanged


# ── Bookmark metrics ──────────────────────────────────────────────────────────


class TestBookmarkMetrics:
    def test_add_bookmark_increments_counter(self, db_session, make_user_token):
        from backend.services.bookmark_service import BookmarkService
        from backend.services.post_service import PostService

        author, _ = make_user_token(role="contributor")
        reader, _ = make_user_token(role="reader")
        post = PostService.create(author.id, "Bookmark Metrics Post")
        PostService.publish(post)
        before = _val("openblog_bookmarks_created_total")
        BookmarkService.add(reader.id, post.id)
        assert _val("openblog_bookmarks_created_total") == before + 1


# ── Metrics endpoint (only when explicitly enabled) ───────────────────────────


@pytest.fixture(scope="module")
def metrics_client():
    """A test client for an app with METRICS_ENABLED=True.

    Module-scoped so that PrometheusMetrics (and its metric families) are
    only created once even when this fixture is used by multiple tests.

    ``init_metrics`` now uses a per-app guard (``app.extensions``) rather than
    a global singleton, so it is safe to call for a second app after the
    integration-test ``live_client`` fixture has already initialised metrics
    for the development app.  The underlying Prometheus metric families stay in
    the shared global REGISTRY; subsequent app instances get a lightweight
    ``/metrics`` route that calls ``generate_latest()`` directly — no
    ``ValueError: Duplicated timeseries`` is raised.
    """
    import backend.utils.metrics as _m
    from backend.app import create_app
    from backend.utils.metrics import init_metrics

    metrics_app = create_app("testing")
    metrics_app.config["METRICS_ENABLED"] = True
    init_metrics(metrics_app)

    yield metrics_app.test_client()

    # Teardown: remove per-app extension state so a future call to
    # init_metrics for this specific app object would re-run.  We intentionally
    # do NOT reset _m._flask_metrics here: resetting it would allow
    # PrometheusMetrics to be constructed again for another app, which would
    # raise ValueError: Duplicated timeseries for the already-registered
    # flask_http_request_* metric families.
    metrics_app.extensions.pop("_openblog_prometheus_metrics", None)


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_200(self, metrics_client):
        resp = metrics_client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_endpoint_content_type(self, metrics_client):
        resp = metrics_client.get("/metrics")
        assert "text/plain" in resp.content_type

    def test_metrics_output_contains_openblog_metrics(self, metrics_client):
        resp = metrics_client.get("/metrics")
        body = resp.get_data(as_text=True)
        assert "openblog_posts_created_total" in body
        assert "openblog_user_registrations_total" in body
        assert "openblog_db_query_duration_seconds_bucket" in body

    def test_no_metrics_endpoint_when_disabled(self, client):
        """Default test client (METRICS_ENABLED=False) has no /metrics route."""
        resp = client.get("/metrics")
        assert resp.status_code == 404
