"""Tests for check_contribution_badges threshold evaluation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services.badge_service import BadgeService


@pytest.fixture()
def user(make_user_token):
    u, _ = make_user_token()
    return u


@pytest.fixture(autouse=True)
def seeded(db_session):
    BadgeService.seed_defaults()


def _run_check(
    user_id,
    *,
    revisions=0,
    benchmarks=0,
    ab_created=0,
    ab_wins=0,
    upvotes=0,
    streak=0,
    ontology_nodes=0,
    workspace_id=None,
):
    summary = {
        "revisions_accepted": revisions,
        "benchmarks_run": benchmarks,
        "ab_experiments_created": ab_created,
    }
    streak_data = {"current_streak": streak, "longest_streak": streak}
    with (
        patch(
            "backend.services.badge_service._count_ontology_nodes",
            return_value=ontology_nodes,
        ),
        patch("backend.services.badge_service._count_ab_wins", return_value=ab_wins),
        patch(
            "backend.services.badge_service._count_received_upvotes",
            return_value=upvotes,
        ),
    ):
        from backend.services import user_analytics_service as uas

        with (
            patch.object(uas, "build_user_contribution_summary", return_value=summary),
            patch.object(uas, "compute_contribution_streak", return_value=streak_data),
        ):
            return BadgeService.check_contribution_badges(
                user_id, workspace_id=workspace_id
            )


def _awarded_keys(awarded):
    return {ub.badge.key for ub in awarded}


# -- Revision thresholds ------------------------------------------------------


class TestRevisionThresholds:
    def test_below_threshold_no_badge(self, user, db_session):
        awarded = _run_check(user.id, revisions=0)
        keys = _awarded_keys(awarded)
        assert "first_revision" not in keys
        assert "ten_revisions" not in keys

    def test_at_threshold_1(self, user, db_session):
        awarded = _run_check(user.id, revisions=1)
        assert "first_revision" in _awarded_keys(awarded)

    def test_at_threshold_10(self, user, db_session):
        awarded = _run_check(user.id, revisions=10)
        keys = _awarded_keys(awarded)
        assert "first_revision" in keys
        assert "ten_revisions" in keys

    def test_at_threshold_50(self, user, db_session):
        awarded = _run_check(user.id, revisions=50)
        keys = _awarded_keys(awarded)
        assert "fifty_revisions" in keys


# -- Benchmark threshold ------------------------------------------------------


class TestBenchmarkThresholds:
    def test_zero_benchmarks_no_badge(self, user, db_session):
        awarded = _run_check(user.id, benchmarks=0)
        assert "first_benchmark" not in _awarded_keys(awarded)

    def test_one_benchmark_awards_badge(self, user, db_session):
        awarded = _run_check(user.id, benchmarks=1)
        assert "first_benchmark" in _awarded_keys(awarded)


# -- A/B experiment thresholds -----------------------------------------------


class TestABExperimentThresholds:
    def test_no_ab_no_badge(self, user, db_session):
        awarded = _run_check(user.id, ab_created=0, ab_wins=0)
        assert "first_ab_experiment" not in _awarded_keys(awarded)
        assert "ab_winner" not in _awarded_keys(awarded)

    def test_ab_created_awards_experimenter(self, user, db_session):
        awarded = _run_check(user.id, ab_created=1)
        assert "first_ab_experiment" in _awarded_keys(awarded)

    def test_ab_win_awards_winner_badge(self, user, db_session):
        awarded = _run_check(user.id, ab_wins=1)
        assert "ab_winner" in _awarded_keys(awarded)


# -- Upvote thresholds --------------------------------------------------------


class TestUpvoteThresholds:
    def test_below_10_no_badge(self, user, db_session):
        awarded = _run_check(user.id, upvotes=9)
        assert "ten_upvotes" not in _awarded_keys(awarded)

    def test_exactly_10_awards_rising_voice(self, user, db_session):
        awarded = _run_check(user.id, upvotes=10)
        assert "ten_upvotes" in _awarded_keys(awarded)

    def test_100_upvotes_awards_both(self, user, db_session):
        awarded = _run_check(user.id, upvotes=100)
        keys = _awarded_keys(awarded)
        assert "ten_upvotes" in keys
        assert "hundred_upvotes" in keys


# -- Streak thresholds --------------------------------------------------------


class TestStreakThresholds:
    def test_below_7_no_streak_badge(self, user, db_session):
        awarded = _run_check(user.id, streak=6)
        assert "streak_7" not in _awarded_keys(awarded)

    def test_7_day_streak(self, user, db_session):
        awarded = _run_check(user.id, streak=7)
        assert "streak_7" in _awarded_keys(awarded)

    def test_30_day_streak_awards_both(self, user, db_session):
        awarded = _run_check(user.id, streak=30)
        keys = _awarded_keys(awarded)
        assert "streak_7" in keys
        assert "streak_30" in keys

    def test_100_day_streak_awards_all_three(self, user, db_session):
        awarded = _run_check(user.id, streak=100)
        keys = _awarded_keys(awarded)
        assert "streak_7" in keys
        assert "streak_30" in keys
        assert "streak_100" in keys


# -- Ontology thresholds ------------------------------------------------------


class TestOntologyThresholds:
    def test_below_5_no_badge(self, user, db_session):
        awarded = _run_check(user.id, ontology_nodes=4)
        assert "ontology_explorer_5" not in _awarded_keys(awarded)

    def test_exactly_5_nodes(self, user, db_session):
        awarded = _run_check(user.id, ontology_nodes=5)
        assert "ontology_explorer_5" in _awarded_keys(awarded)

    def test_10_nodes_awards_both(self, user, db_session):
        awarded = _run_check(user.id, ontology_nodes=10)
        keys = _awarded_keys(awarded)
        assert "ontology_explorer_5" in keys
        assert "ontology_explorer_10" in keys
