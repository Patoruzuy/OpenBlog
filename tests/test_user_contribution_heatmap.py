"""Tests for user_analytics_service.build_contribution_heatmap.

Coverage
--------
  HA-001  Empty user: 52 × 7 grid present; total = 0; all cells level 0.
  HA-002  Single published post: correct cell has count 1, level 1.
  HA-003  Level thresholds: 0=none, 1=1, 2=2-3, 3=4-6, 4=7+.
  HA-004  All five contribution sources appear in the heatmap count.
  HA-005  Grid shape is always exactly 52 × 7 entries.
  HA-006  Contribution outside the 365-day window is NOT counted.
  HA-007  Draft posts are excluded (only published_at counted).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.ab_experiment import ABExperiment
from backend.models.ai_review import AIReviewRequest
from backend.models.benchmark import BenchmarkRun, BenchmarkSuite
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.user_analytics_service import build_contribution_heatmap

_ctr = itertools.count(7000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"ha{n}@example.com",
        username=f"hauser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_post(
    author, *, status=PostStatus.published, published_at=None, workspace_id=None
):
    n = _n()
    if published_at is None and status == PostStatus.published:
        published_at = datetime.now(UTC)
    p = Post(
        title=f"HA-Post {n}",
        slug=f"ha-post-{n}",
        kind="article",
        markdown_body="x",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        published_at=published_at,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_revision(post, author, *, reviewed_at=None):
    """Create an accepted revision on *post* authored by *author*."""
    n = _n()
    reviewed_at = reviewed_at or datetime.now(UTC)
    r = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_number=1,
        proposed_markdown="rev",
        summary=f"HA-Rev {n}",
        status=RevisionStatus.accepted,
        reviewed_at=reviewed_at,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


def _make_ai_review(post, user, *, workspace_id=None, created_at=None):
    created_at = created_at or datetime.now(UTC)
    n = _n()
    req = AIReviewRequest(
        workspace_id=workspace_id,
        post_id=post.id,
        requested_by_user_id=user.id,
        review_type="clarity",
        status="queued",
        input_fingerprint=f"fp{n}",
        created_at=created_at,
    )
    _db.session.add(req)
    _db.session.flush()
    return req


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"HA-Suite {n}",
        slug=f"ha-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_bench_run(post, user, suite, *, workspace_id=None, created_at=None):
    created_at = created_at or datetime.now(UTC)
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=post.id,
        prompt_version=1,
        workspace_id=workspace_id,
        status="completed",
        created_by_user_id=user.id,
        created_at=created_at,
    )
    _db.session.add(run)
    _db.session.flush()
    return run


def _make_ab_experiment(
    user, post_a, post_b, suite, *, workspace_id=None, created_at=None
):
    n = _n()
    created_at = created_at or datetime.now(UTC)
    exp = ABExperiment(
        name=f"HA-Exp {n}",
        slug=f"ha-exp-{n}",
        workspace_id=workspace_id,
        suite_id=suite.id,
        variant_a_prompt_post_id=post_a.id,
        variant_a_version=1,
        variant_b_prompt_post_id=post_b.id,
        variant_b_version=1,
        status="draft",
        created_by_user_id=user.id,
        created_at=created_at,
    )
    _db.session.add(exp)
    _db.session.flush()
    return exp


# ── HA-001 ─────────────────────────────────────────────────────────────────────


class TestHeatmapEmptyUser:
    def test_empty_user_returns_full_empty_grid(self, db_session):
        user = _make_user()
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)

        assert result["total"] == 0
        weeks = result["weeks"]
        # Must produce exactly 52 × 7 cells regardless of weekday
        total_cells = sum(len(w) for w in weeks)
        assert total_cells == 52 * 7
        # All levels are 0
        for week in weeks:
            for cell in week:
                assert cell["level"] == 0
                assert cell["count"] == 0


# ── HA-002 ─────────────────────────────────────────────────────────────────────


class TestHeatmapSinglePost:
    def test_post_today_appears_with_level_1(self, db_session):
        import datetime as _dt  # noqa: PLC0415

        user = _make_user()
        today_utc = _dt.datetime.now(UTC).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        _make_post(user, published_at=today_utc)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)

        today_str = _dt.date.today().isoformat()
        matching = [
            cell
            for week in result["weeks"]
            for cell in week
            if cell["date"] == today_str
        ]
        assert len(matching) == 1
        cell = matching[0]
        assert cell["count"] == 1
        assert cell["level"] == 1
        assert result["total"] == 1


# ── HA-003 ─────────────────────────────────────────────────────────────────────


class TestHeatmapLevelThresholds:
    """Verify the fixed level thresholds: 0=none, 1=1, 2=2-3, 3=4-6, 4=7+."""

    def _count_on_date(self, result, target_date_str: str) -> tuple[int, int]:
        for week in result["weeks"]:
            for cell in week:
                if cell["date"] == target_date_str:
                    return cell["count"], cell["level"]
        return 0, 0

    def test_level_boundaries(self, db_session):
        from backend.services.user_analytics_service import _level  # noqa: PLC0415

        assert _level(0) == 0
        assert _level(1) == 1
        assert _level(2) == 2
        assert _level(3) == 2
        assert _level(4) == 3
        assert _level(5) == 3
        assert _level(6) == 3
        assert _level(7) == 4
        assert _level(10) == 4
        assert _level(100) == 4

    def test_multiple_posts_same_day_correct_level(self, db_session):
        import datetime as _dt  # noqa: PLC0415

        user = _make_user()
        base = _dt.datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
        # 4 posts on the same day → level 3
        for _ in range(4):
            _make_post(user, published_at=base)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)
        target = _dt.date.today().isoformat()
        cnt, lvl = self._count_on_date(result, target)
        assert cnt == 4
        assert lvl == 3


# ── HA-004 ─────────────────────────────────────────────────────────────────────


class TestHeatmapAllSources:
    """Each of the 5 contribution sources must be counted."""

    def test_all_five_sources_counted(self, db_session):
        import datetime as _dt  # noqa: PLC0415

        user = _make_user()
        today = _dt.datetime.now(UTC).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        target = _dt.date.today().isoformat()

        # 1. Published post
        post = _make_post(user, published_at=today)
        # 2. Accepted revision
        _make_revision(post, user, reviewed_at=today)
        # 3. AI review request
        _make_ai_review(post, user, created_at=today)
        # 4. Benchmark run
        suite = _make_suite(user)
        _make_bench_run(post, user, suite, created_at=today)
        # 5. A/B experiment
        post2 = _make_post(user, published_at=today)
        _make_ab_experiment(user, post, post2, suite, created_at=today)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)

        # post, post2 (both published), revision, ai_review, bench_run, ab_exp = 6
        for week in result["weeks"]:
            for cell in week:
                if cell["date"] == target:
                    assert cell["count"] == 6
                    assert cell["level"] == 3  # 4-6 → level 3
                    assert result["total"] == 6
                    return
        raise AssertionError(f"No cell found for {target}")


# ── HA-005 ─────────────────────────────────────────────────────────────────────


class TestHeatmapGridShape:
    def test_grid_always_52x7(self, db_session):
        user = _make_user()
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)
        weeks = result["weeks"]
        assert len(weeks) == 52
        for week in weeks:
            assert len(week) == 7

    def test_dates_are_sorted_ascending(self, db_session):
        user = _make_user()
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)
        all_dates = [cell["date"] for week in result["weeks"] for cell in week]
        assert all_dates == sorted(all_dates)

    def test_no_duplicate_dates(self, db_session):
        user = _make_user()
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)
        all_dates = [cell["date"] for week in result["weeks"] for cell in week]
        assert len(all_dates) == len(set(all_dates))


# ── HA-006 ─────────────────────────────────────────────────────────────────────


class TestHeatmapWindowBoundary:
    def test_post_outside_365_day_window_excluded(self, db_session):
        import datetime as _dt  # noqa: PLC0415

        user = _make_user()
        old_date = _dt.datetime.now(UTC) - timedelta(days=400)
        _make_post(user, published_at=old_date)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)
        assert result["total"] == 0

    def test_post_inside_window_counted(self, db_session):
        import datetime as _dt  # noqa: PLC0415

        user = _make_user()
        recent = _dt.datetime.now(UTC) - timedelta(days=30)
        _make_post(user, published_at=recent)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)
        assert result["total"] == 1


# ── HA-007 ─────────────────────────────────────────────────────────────────────


class TestHeatmapDraftExcluded:
    def test_draft_post_not_counted(self, db_session):
        user = _make_user()
        _make_post(user, status=PostStatus.draft, published_at=None)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)
        assert result["total"] == 0
