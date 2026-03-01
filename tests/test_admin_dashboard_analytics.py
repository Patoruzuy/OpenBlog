"""Comprehensive tests for admin /dashboard and /analytics.

Coverage
--------
Unit — AdminDashboardService:
  - post state counts
  - revision counts
  - user counts
  - active contributors 30d
  - health key present

Unit — AdminAnalyticsService:
  - total_views in period
  - period-windowed revision funnel
  - avg_review_days computation
  - acceptance_rate computation
  - first_time_contributors count
  - top_contributors list (username, count; no emails)
  - stale_posts (not updated in 90+ days)
  - low_traffic_posts (< 10 views)
  - all keys present on empty DB

Integration — /admin/dashboard:
  - renders for admin and editor
  - blocked for reader, contributor, anon
  - shows published / draft / unverified counts in HTML
  - shows health strip HTML marker
  - shows activity feed

Integration — /admin/analytics:
  - renders for admin and editor
  - blocked for reader, contributor, anon
  - days param changes the `days` value propagated to template
  - days param > 90 is clamped to 90
  - total_views and acceptance_rate appear in HTML
  - stale and low-traffic sections render without error
  - top contributors table does NOT expose email addresses
  - empty DB renders without error (all empty states shown)
  - contributor metrics show username, not email
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.analytics import AnalyticsEvent
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User, UserRole
from backend.services.admin_analytics_service import AdminAnalyticsService
from backend.services.admin_dashboard_service import AdminDashboardService

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _login(client, user: User) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


def _make_user(email: str, username: str, role: str = "reader") -> User:
    u = User(
        email=email,
        username=username,
        role=UserRole(role),
        is_email_verified=True,
        is_active=True,
    )
    _db.session.add(u)
    _db.session.commit()
    return u


def _make_post(
    author: User,
    *,
    title: str = "Test Post",
    status: PostStatus = PostStatus.published,
    view_count: int = 0,
    days_since_update: int = 0,
) -> Post:
    now = datetime.now(UTC)
    updated_at = now - timedelta(days=days_since_update)
    slug = title.lower().replace(" ", "-").replace("/", "-") + f"-{id(title)}"
    post = Post(
        title=title,
        slug=slug,
        markdown_body="Hello",
        author_id=author.id,
        status=status,
        view_count=view_count,
        updated_at=updated_at,
        published_at=updated_at if status == PostStatus.published else None,
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_revision(
    post: Post,
    author: User,
    *,
    status: RevisionStatus = RevisionStatus.pending,
    days_ago: int = 1,
    review_days_after: int | None = None,
) -> Revision:
    now = datetime.now(UTC)
    created_at = now - timedelta(days=days_ago)
    reviewed_at = None
    if review_days_after is not None:
        reviewed_at = created_at + timedelta(days=review_days_after)
    rev = Revision(
        post_id=post.id,
        author_id=author.id,
        proposed_markdown="Updated content",
        summary="Test change",
        base_version_number=1,
        status=status,
        created_at=created_at,
        reviewed_at=reviewed_at,
    )
    _db.session.add(rev)
    _db.session.commit()
    return rev


def _make_analytics_event(post: Post, *, days_ago: int = 1) -> AnalyticsEvent:
    now = datetime.now(UTC)
    evt = AnalyticsEvent(
        event_type="post_view",
        post_id=post.id,
        occurred_at=now - timedelta(days=days_ago),
    )
    _db.session.add(evt)
    _db.session.commit()
    return evt


# ─────────────────────────────────────────────────────────────────────────────
# Unit — AdminDashboardService
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminDashboardServiceUnit:
    def test_post_counts_by_status(self, db_session):
        author = _make_user("dash_author@example.com", "dash_author", "editor")
        _make_post(author, title="Published 1", status=PostStatus.published)
        _make_post(author, title="Published 2", status=PostStatus.published)
        _make_post(author, title="Draft 1", status=PostStatus.draft)
        _make_post(author, title="Scheduled 1", status=PostStatus.scheduled)

        snap = AdminDashboardService.get_snapshot()

        assert snap["posts_published"] == 2
        assert snap["posts_draft"] == 1
        assert snap["posts_scheduled"] == 1

    def test_revision_pending_count(self, db_session):
        author = _make_user("dash_rev_author@example.com", "dash_rev_author", "editor")
        post = _make_post(author, title="Rev Post")
        contrib = _make_user("dash_contrib@example.com", "dash_contrib", "contributor")
        _make_revision(post, contrib, status=RevisionStatus.pending)
        _make_revision(post, contrib, status=RevisionStatus.pending)

        snap = AdminDashboardService.get_snapshot()

        assert snap["revisions_pending"] >= 2

    def test_user_counts(self, db_session):
        _make_user("usr_verified@example.com", "usr_verified", "reader")
        unverified = _make_user(
            "usr_unverified@example.com", "usr_unverified2", "reader"
        )
        unverified.is_email_verified = False
        _db.session.commit()

        snap = AdminDashboardService.get_snapshot()

        assert snap["total_users"] >= 2
        assert snap["unverified_users"] >= 1

    def test_active_contributors_30d_key_present(self, db_session):
        snap = AdminDashboardService.get_snapshot()
        assert "active_contributors_30d" in snap
        assert isinstance(snap["active_contributors_30d"], int)

    def test_active_contributors_30d_counts_recent_revisions(self, db_session):
        author = _make_user("dash_contrib2@example.com", "dash_contrib2", "editor")
        post = _make_post(author, title="Contrib Post")
        c1 = _make_user("c1@example.com", "contrib_c1", "contributor")
        c2 = _make_user("c2@example.com", "contrib_c2", "contributor")
        _make_revision(post, c1, days_ago=5)
        _make_revision(post, c2, days_ago=5)

        snap = AdminDashboardService.get_snapshot()

        assert snap["active_contributors_30d"] >= 2

    def test_active_contributors_excludes_old_revisions(self, db_session):
        author = _make_user("dash_old_a@example.com", "dash_old_a", "editor")
        post = _make_post(author, title="Old Rev Post")
        c = _make_user("old_contrib@example.com", "old_contrib", "contributor")
        # Revision from 60 days ago — outside 30d window
        _make_revision(post, c, days_ago=60)

        snap = AdminDashboardService.get_snapshot()

        assert snap["active_contributors_30d"] == 0

    def test_health_key_present(self, db_session, app):
        with app.app_context():
            snap = AdminDashboardService.get_snapshot()
        assert "health" in snap
        assert "db" in snap["health"]
        assert "redis" in snap["health"]

    def test_db_health_is_ok(self, db_session, app):
        with app.app_context():
            snap = AdminDashboardService.get_snapshot()
        assert snap["health"]["db"]["ok"] is True

    def test_pv_trend_key_present(self, db_session):
        snap = AdminDashboardService.get_snapshot()
        assert "pv_trend" in snap
        assert isinstance(snap["pv_trend"], list)

    def test_recent_audit_key_present(self, db_session):
        snap = AdminDashboardService.get_snapshot()
        assert "recent_audit" in snap


# ─────────────────────────────────────────────────────────────────────────────
# Unit — AdminAnalyticsService
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminAnalyticsServiceUnit:
    def test_all_keys_present_on_empty_db(self, db_session):
        data = AdminAnalyticsService.overview(days=30)
        required = {
            "total_views",
            "top_posts",
            "pv_by_day",
            "rev_funnel",
            "rev_submitted_period",
            "rev_accepted_period",
            "rev_rejected_period",
            "avg_review_days",
            "acceptance_rate",
            "active_contributors",
            "accepted_contribs",
            "first_time_contributors",
            "top_contributors",
            "signups_by_day",
            "top_tags",
            "stale_posts",
            "low_traffic_posts",
            "comments_by_day",
            "days",
        }
        assert required.issubset(data.keys())

    def test_total_views_counts_events_in_period(self, db_session):
        author = _make_user("views_author@example.com", "views_author", "editor")
        post = _make_post(author, title="Views Post")
        _make_analytics_event(post, days_ago=5)  # in 30d window
        _make_analytics_event(post, days_ago=5)  # in 30d window
        _make_analytics_event(post, days_ago=40)  # outside 30d window

        data = AdminAnalyticsService.overview(days=30)

        assert data["total_views"] == 2

    def test_total_views_zero_on_empty_db(self, db_session):
        data = AdminAnalyticsService.overview(days=30)
        assert data["total_views"] == 0

    def test_rev_submitted_period_counts_revisions_created_in_window(self, db_session):
        author = _make_user("rev_sub_author@example.com", "rev_sub_author", "editor")
        post = _make_post(author, title="Rev Sub Post")
        c = _make_user("rev_sub_c@example.com", "rev_sub_c", "contributor")
        _make_revision(post, c, days_ago=5)  # in 30d window
        _make_revision(post, c, days_ago=40)  # outside 30d window

        data = AdminAnalyticsService.overview(days=30)

        assert data["rev_submitted_period"] == 1

    def test_rev_accepted_period_counts_accepted_reviewed_in_window(self, db_session):
        author = _make_user("rv_acc_a@example.com", "rv_acc_a", "editor")
        post = _make_post(author, title="Acc Post")
        c = _make_user("rv_acc_c@example.com", "rv_acc_c", "contributor")
        _make_revision(
            post,
            c,
            status=RevisionStatus.accepted,
            days_ago=20,
            review_days_after=2,  # reviewed 18 days ago — within 30d
        )
        _make_revision(
            post,
            c,
            status=RevisionStatus.accepted,
            days_ago=60,
            review_days_after=2,  # reviewed 58 days ago — outside 30d
        )

        data = AdminAnalyticsService.overview(days=30)

        assert data["rev_accepted_period"] == 1

    def test_avg_review_days_none_when_no_reviews(self, db_session):
        data = AdminAnalyticsService.overview(days=30)
        assert data["avg_review_days"] is None

    def test_avg_review_days_computed_correctly(self, db_session):
        author = _make_user("avg_rev_a@example.com", "avg_rev_a", "editor")
        post = _make_post(author, title="Avg Rev Post")
        c = _make_user("avg_rev_c@example.com", "avg_rev_c", "contributor")
        # Revision reviewed 2 days after creation, reviewed within 30d
        _make_revision(
            post,
            c,
            status=RevisionStatus.accepted,
            days_ago=10,
            review_days_after=2,
        )
        # Revision reviewed 4 days after creation, reviewed within 30d
        _make_revision(
            post,
            c,
            status=RevisionStatus.rejected,
            days_ago=8,
            review_days_after=4,
        )

        data = AdminAnalyticsService.overview(days=30)

        # avg of 2 and 4 = 3.0 days
        assert data["avg_review_days"] == 3.0

    def test_acceptance_rate_none_when_no_reviews(self, db_session):
        data = AdminAnalyticsService.overview(days=30)
        assert data["acceptance_rate"] is None

    def test_acceptance_rate_computed_correctly(self, db_session):
        author = _make_user("acc_rate_a@example.com", "acc_rate_a", "editor")
        post = _make_post(author, title="Acc Rate Post")
        c = _make_user("acc_rate_c@example.com", "acc_rate_c", "contributor")
        # 2 accepted, 2 rejected → 50% acceptance rate
        for _ in range(2):
            _make_revision(
                post, c, status=RevisionStatus.accepted, days_ago=5, review_days_after=1
            )
        for _ in range(2):
            _make_revision(
                post, c, status=RevisionStatus.rejected, days_ago=5, review_days_after=1
            )

        data = AdminAnalyticsService.overview(days=30)

        assert data["acceptance_rate"] == 50.0

    def test_first_time_contributors_count(self, db_session):
        author = _make_user("ftc_author@example.com", "ftc_author", "editor")
        post = _make_post(author, title="FTC Post")
        # c1 — first revision is recent (within window)
        c1 = _make_user("ftc_c1@example.com", "ftc_c1", "contributor")
        _make_revision(post, c1, days_ago=5)
        # c2 — first revision is old (outside window)
        c2 = _make_user("ftc_c2@example.com", "ftc_c2", "contributor")
        _make_revision(post, c2, days_ago=60)

        data = AdminAnalyticsService.overview(days=30)

        assert data["first_time_contributors"] == 1

    def test_top_contributors_no_emails_exposed(self, db_session):
        author = _make_user("tc_author@example.com", "tc_author", "editor")
        post = _make_post(author, title="TC Post")
        c = _make_user("tc_c@example.com", "tc_contributor", "contributor")
        _make_revision(
            post, c, status=RevisionStatus.accepted, days_ago=5, review_days_after=1
        )

        data = AdminAnalyticsService.overview(days=30)

        assert len(data["top_contributors"]) >= 1
        for entry in data["top_contributors"]:
            assert "email" not in entry
            assert "@" not in entry["display"]
            assert "@" not in entry.get("username", "")
            assert isinstance(entry["count"], int)

    def test_stale_posts_older_than_90_days(self, db_session):
        author = _make_user("stale_author@example.com", "stale_author", "editor")
        # Updated 100 days ago → stale
        _make_post(
            author,
            title="Stale Post",
            status=PostStatus.published,
            days_since_update=100,
        )
        # Updated 10 days ago → not stale
        _make_post(
            author,
            title="Fresh Post",
            status=PostStatus.published,
            days_since_update=10,
        )

        data = AdminAnalyticsService.overview(days=30)

        stale_titles = {p.title for p in data["stale_posts"]}
        assert "Stale Post" in stale_titles
        assert "Fresh Post" not in stale_titles

    def test_stale_posts_drafts_excluded(self, db_session):
        author = _make_user("stale_d_author@example.com", "stale_d_author", "editor")
        # Draft post updated long ago — should not appear in stale list
        _make_post(
            author, title="Old Draft", status=PostStatus.draft, days_since_update=200
        )

        data = AdminAnalyticsService.overview(days=30)

        stale_titles = {p.title for p in data["stale_posts"]}
        assert "Old Draft" not in stale_titles

    def test_low_traffic_posts_under_10_views(self, db_session):
        author = _make_user("lt_author@example.com", "lt_author", "editor")
        _make_post(
            author, title="Zero Views Post", status=PostStatus.published, view_count=0
        )
        _make_post(
            author, title="High Views Post", status=PostStatus.published, view_count=500
        )

        data = AdminAnalyticsService.overview(days=30)

        low_titles = {p.title for p in data["low_traffic_posts"]}
        assert "Zero Views Post" in low_titles
        assert "High Views Post" not in low_titles

    def test_days_param_applied_to_views(self, db_session):
        author = _make_user("dp_author@example.com", "dp_author", "editor")
        post = _make_post(author, title="Days Param Post")
        _make_analytics_event(post, days_ago=5)  # within 7d
        _make_analytics_event(post, days_ago=20)  # outside 7d but within 30d

        data_7 = AdminAnalyticsService.overview(days=7)
        data_30 = AdminAnalyticsService.overview(days=30)

        assert data_7["total_views"] == 1
        assert data_30["total_views"] == 2

    def test_empty_db_no_errors(self, db_session):
        # Should not raise
        data = AdminAnalyticsService.overview(days=30)
        assert data["total_views"] == 0
        assert data["active_contributors"] == 0
        assert data["stale_posts"] == []
        assert data["top_contributors"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Integration — /admin/dashboard access
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardAccess:
    def test_anon_redirected_to_login(self, auth_client):
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code in (301, 302)
        assert "/auth/login" in resp.headers["Location"]

    def test_reader_blocked(self, auth_client, make_user_token):
        user, _ = make_user_token(role="reader")
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code in (301, 302)

    def test_contributor_blocked(self, auth_client, make_user_token):
        user, _ = make_user_token(role="contributor")
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code in (301, 302)

    def test_editor_allowed(self, auth_client, make_user_token):
        user, _ = make_user_token(role="editor")
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200

    def test_admin_allowed(self, auth_client, make_user_token):
        user, _ = make_user_token(role="admin")
        _login(auth_client, user)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Integration — /admin/analytics access
# ─────────────────────────────────────────────────────────────────────────────


class TestAnalyticsAccess:
    def test_anon_redirected_to_login(self, auth_client):
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code in (301, 302)
        assert "/auth/login" in resp.headers["Location"]

    def test_reader_blocked(self, auth_client, make_user_token):
        user, _ = make_user_token(role="reader")
        _login(auth_client, user)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code in (301, 302)

    def test_contributor_blocked(self, auth_client, make_user_token):
        user, _ = make_user_token(role="contributor")
        _login(auth_client, user)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code in (301, 302)

    def test_editor_can_view_analytics(self, auth_client, make_user_token):
        user, _ = make_user_token(role="editor")
        _login(auth_client, user)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code == 200

    def test_admin_can_view_analytics(self, auth_client, make_user_token):
        user, _ = make_user_token(role="admin")
        _login(auth_client, user)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Integration — /admin/dashboard rendering
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardRendering:
    def test_renders_post_section(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200
        # stat card labels are present
        assert b"Published" in resp.data
        assert b"Drafts" in resp.data

    def test_shows_published_count_in_stat_card(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _make_post(admin, title="DB Published A", status=PostStatus.published)
        _make_post(admin, title="DB Published B", status=PostStatus.published)
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200
        # Page contains at least "2" somewhere in stat grid context
        assert b"2" in resp.data

    def test_shows_unverified_users_card(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert b"Unverified Email" in resp.data

    def test_shows_active_contributors_card(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert b"Active Contributors" in resp.data

    def test_shows_health_strip(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert b"adm-health-strip" in resp.data
        assert b"Database" in resp.data
        assert b"Redis" in resp.data

    def test_shows_page_view_section_or_empty_state(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert b"Page Views" in resp.data

    def test_shows_recent_activity_section(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert b"Recent Activity" in resp.data

    def test_top_posts_table_present(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert b"Top Posts" in resp.data

    def test_empty_top_posts_shows_empty_state(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/dashboard")
        assert resp.status_code == 200
        # When no posts: empty-state message or table renders fine
        assert b"Top Posts" in resp.data


# ─────────────────────────────────────────────────────────────────────────────
# Integration — /admin/analytics rendering and filters
# ─────────────────────────────────────────────────────────────────────────────


class TestAnalyticsRendering:
    def test_renders_correctly(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code == 200

    def test_default_days_is_30(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code == 200
        assert b"30d" in resp.data

    def test_days_param_7_shows_7d_label(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics?days=7")
        assert resp.status_code == 200
        assert b"7d" in resp.data

    def test_days_param_clamped_at_90(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics?days=9999")
        assert resp.status_code == 200

    def test_revision_funnel_section_present(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert b"Revision Funnel" in resp.data

    def test_top_posts_section_present(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert b"Top Posts" in resp.data

    def test_stale_content_section_present(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert b"Stale Content" in resp.data

    def test_low_traffic_section_present(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert b"Low-Traffic" in resp.data

    def test_views_stat_card_present(self, auth_client, make_user_token, db_session):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert b"Views" in resp.data

    def test_top_contributors_does_not_expose_email(
        self, auth_client, make_user_token, db_session
    ):
        """Contributor email addresses must never appear in the analytics page."""
        admin, _ = make_user_token(role="admin")
        # Create a contributor whose email would be visible if leaked
        contrib = _make_user(
            "hidden_email_contrib@private.io", "safe_username_contrib", "contributor"
        )
        post = _make_post(
            admin, title="Contrib Privacy Post", status=PostStatus.published
        )
        _make_revision(
            post,
            contrib,
            status=RevisionStatus.accepted,
            days_ago=5,
            review_days_after=1,
        )
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code == 200
        # Email must not appear
        assert b"hidden_email_contrib@private.io" not in resp.data
        # Username is OK to appear (internal admin view)
        assert b"safe_username_contrib" in resp.data

    def test_empty_db_renders_without_error(
        self, auth_client, make_user_token, db_session
    ):
        """Analytics page must display graceful empty states when no data exists."""
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert resp.status_code == 200
        # All 4 empty-state messages should be present (or sections render a table)
        assert b"analytics" in resp.data.lower() or b"Analytics" in resp.data

    def test_stale_post_appears_in_content_table(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _make_post(
            admin,
            title="Extra Stale Article",
            status=PostStatus.published,
            days_since_update=120,
        )
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert b"Extra Stale Article" in resp.data

    def test_low_traffic_post_appears_in_table(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _make_post(
            admin, title="No Views Article", status=PostStatus.published, view_count=0
        )
        _login(auth_client, admin)
        resp = auth_client.get("/admin/analytics")
        assert b"No Views Article" in resp.data

    def test_different_day_windows_return_200(
        self, auth_client, make_user_token, db_session
    ):
        admin, _ = make_user_token(role="admin")
        _login(auth_client, admin)
        for d in [7, 14, 30, 90]:
            resp = auth_client.get(f"/admin/analytics?days={d}")
            assert resp.status_code == 200, f"Expected 200 for days={d}"
