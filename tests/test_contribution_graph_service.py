"""Tests for ContributionGraphService."""

from __future__ import annotations

import datetime

import pytest

from backend.models.post import Post, PostStatus
from backend.services.contribution_graph_service import ContributionGraphService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@example.com", "alice")
    return user


def _add_post(db, author_id: int, slug: str, days_ago: int) -> Post:
    published_at = datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)
    post = Post(
        author_id=author_id,
        title=f"Post {slug}",
        slug=slug,
        markdown_body="# Body",
        status=PostStatus.published,
        published_at=published_at,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── get_contributions ─────────────────────────────────────────────────────────


class TestGetContributions:
    def test_returns_52_weeks(self, alice, db_session):
        result = ContributionGraphService.get_contributions(alice.id)
        assert len(result["weeks"]) == 52

    def test_each_week_has_7_or_fewer_cells(self, alice, db_session):
        result = ContributionGraphService.get_contributions(alice.id)
        for week in result["weeks"]:
            assert 1 <= len(week) <= 7

    def test_zero_contributions_without_posts(self, alice, db_session):
        result = ContributionGraphService.get_contributions(alice.id)
        assert result["total"] == 0
        for week in result["weeks"]:
            for cell in week:
                assert cell["count"] == 0
                assert cell["level"] == 0

    def test_counts_recent_posts(self, alice, db_session):
        from backend.extensions import db

        _add_post(db, alice.id, "recent-1", days_ago=5)
        _add_post(db, alice.id, "recent-2", days_ago=10)
        result = ContributionGraphService.get_contributions(alice.id)
        assert result["total"] == 2

    def test_excludes_old_posts(self, alice, db_session):
        from backend.extensions import db

        _add_post(db, alice.id, "old-post", days_ago=400)
        result = ContributionGraphService.get_contributions(alice.id)
        assert result["total"] == 0

    def test_public_and_self_view_show_same_total(self, alice, db_session):
        from backend.extensions import db

        _add_post(db, alice.id, "pub-post-1", days_ago=5)
        _add_post(db, alice.id, "pub-post-2", days_ago=6)

        public = ContributionGraphService.get_contributions(
            alice.id, viewer_is_self=False
        )
        self_view = ContributionGraphService.get_contributions(
            alice.id, viewer_is_self=True
        )

        assert public["total"] == self_view["total"] == 2

    def test_self_view_returns_expected_total(self, alice, db_session):
        from backend.extensions import db

        _add_post(db, alice.id, "self-post-1", days_ago=3)
        _add_post(db, alice.id, "self-post-2", days_ago=7)

        result = ContributionGraphService.get_contributions(
            alice.id, viewer_is_self=True
        )
        assert result["total"] == 2

    def test_cell_level_increases_with_count(self, alice, db_session):
        from backend.extensions import db

        # Post 5 items on a single day — level should be > 0
        for i in range(5):
            _add_post(db, alice.id, f"busy-day-{i}", days_ago=2)
        result = ContributionGraphService.get_contributions(alice.id)
        max_level = max(cell["level"] for week in result["weeks"] for cell in week)
        assert max_level > 0

    def test_cell_dates_are_strings(self, alice, db_session):
        result = ContributionGraphService.get_contributions(alice.id)
        for week in result["weeks"]:
            for cell in week:
                datetime.date.fromisoformat(cell["date"])  # raises if invalid
