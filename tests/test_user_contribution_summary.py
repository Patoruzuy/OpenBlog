"""Tests for user_analytics_service.build_user_contribution_summary.

Coverage
--------
  SU-001  Empty user: all counts zero.
  SU-002  Published posts counted; draft posts excluded.
  SU-003  Revisions: submitted (all) and accepted (subset) counted separately.
  SU-004  AI review requests counted.
  SU-005  Benchmark runs counted.
  SU-006  A/B experiments counted.
  SU-007  Other users' data not included.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.ab_experiment import ABExperiment
from backend.models.ai_review import AIReviewRequest
from backend.models.benchmark import BenchmarkRun, BenchmarkSuite
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.user_analytics_service import build_user_contribution_summary

_ctr = itertools.count(8000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"su{n}@example.com",
        username=f"suuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_post(author, *, status=PostStatus.published, workspace_id=None):
    n = _n()
    p = Post(
        title=f"SU-Post {n}",
        slug=f"su-post-{n}",
        kind="article",
        markdown_body="x",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        published_at=datetime.now(UTC) if status == PostStatus.published else None,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_revision(post, author, *, status=RevisionStatus.accepted):
    n = _n()
    r = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_number=1,
        proposed_markdown="rev",
        summary=f"SU-Rev {n}",
        status=status,
        reviewed_at=datetime.now(UTC) if status == RevisionStatus.accepted else None,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


def _make_ai_review(post, user, *, workspace_id=None):
    n = _n()
    req = AIReviewRequest(
        workspace_id=workspace_id,
        post_id=post.id,
        requested_by_user_id=user.id,
        review_type="clarity",
        status="queued",
        input_fingerprint=f"fp-su{n}",
        created_at=datetime.now(UTC),
    )
    _db.session.add(req)
    _db.session.flush()
    return req


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"SU-Suite {n}",
        slug=f"su-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_bench_run(post, user, suite, *, workspace_id=None):
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=post.id,
        prompt_version=1,
        workspace_id=workspace_id,
        status="completed",
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(run)
    _db.session.flush()
    return run


def _make_ab_experiment(user, post_a, post_b, suite, *, workspace_id=None):
    n = _n()
    exp = ABExperiment(
        name=f"SU-Exp {n}",
        slug=f"su-exp-{n}",
        workspace_id=workspace_id,
        suite_id=suite.id,
        variant_a_prompt_post_id=post_a.id,
        variant_a_version=1,
        variant_b_prompt_post_id=post_b.id,
        variant_b_version=1,
        status="draft",
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(exp)
    _db.session.flush()
    return exp


# ── SU-001 ─────────────────────────────────────────────────────────────────────


class TestSummaryEmptyUser:
    def test_all_zeros_for_new_user(self, db_session):
        user = _make_user()
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["posts_published"] == 0
        assert result["revisions_submitted"] == 0
        assert result["revisions_accepted"] == 0
        assert result["ai_reviews_requested"] == 0
        assert result["benchmarks_run"] == 0
        assert result["ab_experiments_created"] == 0


# ── SU-002 ─────────────────────────────────────────────────────────────────────


class TestSummaryPostsCounted:
    def test_published_posts_counted_drafts_excluded(self, db_session):
        user = _make_user()
        _make_post(user, status=PostStatus.published)
        _make_post(user, status=PostStatus.published)
        _make_post(user, status=PostStatus.draft)  # excluded
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["posts_published"] == 2


# ── SU-003 ─────────────────────────────────────────────────────────────────────


class TestSummaryRevisions:
    def test_submitted_and_accepted_counted_separately(self, db_session):
        user = _make_user()
        post = _make_post(user)
        # 2 accepted
        _make_revision(post, user, status=RevisionStatus.accepted)
        _make_revision(post, user, status=RevisionStatus.accepted)
        # 1 pending
        _make_revision(post, user, status=RevisionStatus.pending)
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["revisions_submitted"] == 3
        assert result["revisions_accepted"] == 2


# ── SU-004 ─────────────────────────────────────────────────────────────────────


class TestSummaryAIReviews:
    def test_ai_reviews_counted(self, db_session):
        user = _make_user()
        post = _make_post(user)
        _make_ai_review(post, user)
        _make_ai_review(post, user)
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["ai_reviews_requested"] == 2


# ── SU-005 ─────────────────────────────────────────────────────────────────────


class TestSummaryBenchmarkRuns:
    def test_benchmark_runs_counted(self, db_session):
        user = _make_user()
        post = _make_post(user)
        suite = _make_suite(user)
        _make_bench_run(post, user, suite)
        _make_bench_run(post, user, suite)
        _make_bench_run(post, user, suite)
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["benchmarks_run"] == 3


# ── SU-006 ─────────────────────────────────────────────────────────────────────


class TestSummaryABExperiments:
    def test_ab_experiments_counted(self, db_session):
        user = _make_user()
        post_a = _make_post(user)
        post_b = _make_post(user)
        suite = _make_suite(user)
        _make_ab_experiment(user, post_a, post_b, suite)
        _make_ab_experiment(user, post_a, post_b, suite)
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["ab_experiments_created"] == 2


# ── SU-007 ─────────────────────────────────────────────────────────────────────


class TestSummaryOtherUserExcluded:
    def test_other_users_data_not_included(self, db_session):
        user = _make_user()
        other = _make_user()
        post = _make_post(other)
        suite = _make_suite(other)
        # other user's contributions
        _make_revision(post, other, status=RevisionStatus.accepted)
        _make_ai_review(post, other)
        _make_bench_run(post, other, suite)
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["posts_published"] == 0
        assert result["revisions_submitted"] == 0
        assert result["ai_reviews_requested"] == 0
        assert result["benchmarks_run"] == 0
