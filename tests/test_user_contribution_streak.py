"""Tests for user_analytics_service.compute_contribution_streak.

Coverage
--------
  ST-001  No activity: current = 0, longest = 0.
  ST-002  Single-day activity today: current = 1, longest = 1.
  ST-003  N consecutive days including today: current = N, longest = N.
  ST-004  Gap breaks current streak; prior run preserved as longest.
  ST-005  Activity only yesterday (no contribution today): current = 1.
  ST-006  Activity today + yesterday: current = 2.
  ST-007  Longest streak correctly computed across non-contiguous runs.
  ST-008  Activity only 2 days ago (gap today + yesterday): current = 0.
"""

from __future__ import annotations

import itertools
from datetime import UTC, timedelta

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services.user_analytics_service import compute_contribution_streak

_ctr = itertools.count(10000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"st{n}@example.com",
        username=f"stuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _post_on(author, days_ago: int):
    """Create a published post with published_at = today − days_ago."""
    import datetime as _dt  # noqa: PLC0415

    n = _n()
    target = _dt.datetime.now(UTC) - timedelta(days=days_ago)
    target = target.replace(hour=12, minute=0, second=0, microsecond=0)
    p = Post(
        title=f"ST-Post {n}",
        slug=f"st-post-{n}",
        kind="article",
        markdown_body="x",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=None,
        published_at=target,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


# ── ST-001 ─────────────────────────────────────────────────────────────────────


class TestStreakNoActivity:
    def test_no_data_returns_zero(self, db_session):
        user = _make_user()
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 0
        assert result["longest_streak"] == 0


# ── ST-002 ─────────────────────────────────────────────────────────────────────


class TestStreakSingleDay:
    def test_post_today_streak_is_1(self, db_session):
        user = _make_user()
        _post_on(user, 0)  # today
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 1
        assert result["longest_streak"] == 1


# ── ST-003 ─────────────────────────────────────────────────────────────────────


class TestStreakConsecutiveDays:
    def test_five_consecutive_days_ending_today(self, db_session):
        user = _make_user()
        for d in range(5):  # today, yesterday, 2, 3, 4 days ago
            _post_on(user, d)
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 5
        assert result["longest_streak"] == 5


# ── ST-004 ─────────────────────────────────────────────────────────────────────


class TestStreakGapBreaksCurrent:
    def test_gap_breaks_current_preserves_longest(self, db_session):
        user = _make_user()
        # Old run: 3 consecutive days, 10-12 days ago
        _post_on(user, 12)
        _post_on(user, 11)
        _post_on(user, 10)
        # Gap (days 3-9 missing)
        # Recent run: only today
        _post_on(user, 0)
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 1
        assert result["longest_streak"] == 3


# ── ST-005 ─────────────────────────────────────────────────────────────────────


class TestStreakYesterdayOnly:
    def test_only_yesterday_current_streak_is_1(self, db_session):
        user = _make_user()
        _post_on(user, 1)  # yesterday
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 1


# ── ST-006 ─────────────────────────────────────────────────────────────────────


class TestStreakTodayAndYesterday:
    def test_today_and_yesterday_current_is_2(self, db_session):
        user = _make_user()
        _post_on(user, 0)  # today
        _post_on(user, 1)  # yesterday
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 2
        assert result["longest_streak"] == 2


# ── ST-007 ─────────────────────────────────────────────────────────────────────


class TestStreakLongestAcrossRuns:
    def test_longest_spans_best_historical_run(self, db_session):
        user = _make_user()
        # Long run 20 days ago: 7 consecutive days
        for d in range(7):
            _post_on(user, 20 + d)
        # Short current run: 2 days
        _post_on(user, 0)
        _post_on(user, 1)
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 2
        assert result["longest_streak"] == 7


# ── ST-008 ─────────────────────────────────────────────────────────────────────


class TestStreakGapYesterday:
    def test_two_day_gap_gives_zero_current(self, db_session):
        user = _make_user()
        _post_on(user, 2)  # 2 days ago — no today, no yesterday
        _db.session.commit()

        result = compute_contribution_streak(user.id, public_only=True)

        assert result["current_streak"] == 0
        assert result["longest_streak"] == 1
