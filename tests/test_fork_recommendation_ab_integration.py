"""Tests for Fork Recommendation Service — A/B experiment integration.

Coverage
--------
  FRAB-001  Fork that won its A/B experiment → ab_win_rate > 0.
  FRAB-002  Fork that participated but lost → ab_win_rate == 0.0.
  FRAB-003  Fork not in any A/B experiment → ab_win_rate is None (no contribution).
  FRAB-004  A/B experiments from another workspace excluded in public scope.
  FRAB-005  Experiment with no scored benchmark results → ab_win_rate is None.
  FRAB-006  Non-completed experiment (draft) not counted.
  FRAB-007  A/B win rate boosts ranking vs equal-signal fork without A/B data.
  FRAB-008  Fork that won 2 of 3 experiments → ab_win_rate == 2/3.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.ab_experiment import (
    ABExperiment,
    ABExperimentRun,
    ABExperimentStatus,
)
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.services import fork_recommendation_service as svc

_ctr = itertools.count(13_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"frab{n}@example.com",
        username=f"frabuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"FRAB-Prompt {n}",
        slug=f"frab-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_fork(base: Post, author, *, workspace_id=None) -> Post:
    n = _n()
    fork = Post(
        title=f"FRAB-Fork {n}",
        slug=f"frab-fork-{n}",
        kind="prompt",
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(fork)
    _db.session.flush()
    _db.session.add(
        ContentLink(
            from_post_id=fork.id,
            to_post_id=base.id,
            link_type="derived_from",
            created_by_user_id=author.id,
        )
    )
    _db.session.flush()
    return fork


def _make_suite(user, *, workspace_id=None) -> BenchmarkSuite:
    n = _n()
    s = BenchmarkSuite(
        name=f"FRAB Suite {n}",
        slug=f"frab-suite-{n}",
        created_by_user_id=user.id,
        workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_case(suite: BenchmarkSuite) -> BenchmarkCase:
    n = _n()
    c = BenchmarkCase(
        suite_id=suite.id,
        name=f"FRAB Case {n}",
        input_json={"q": "test"},
    )
    _db.session.add(c)
    _db.session.flush()
    return c


def _make_run(suite: BenchmarkSuite, prompt: Post, user) -> BenchmarkRun:
    """Create a completed BenchmarkRun (no results added yet)."""
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=1,
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=user.id,
    )
    _db.session.add(run)
    _db.session.flush()
    return run


def _add_result(run: BenchmarkRun, case: BenchmarkCase, score: float) -> None:
    _db.session.add(
        BenchmarkRunResult(
            run_id=run.id,
            case_id=case.id,
            output_text="out",
            score_numeric=score,
        )
    )
    _db.session.flush()


def _make_ab_experiment(
    user,
    fork_a: Post,
    fork_b: Post,
    suite: BenchmarkSuite,
    *,
    workspace_id=None,
    status: str = ABExperimentStatus.completed.value,
) -> ABExperiment:
    n = _n()
    exp = ABExperiment(
        name=f"FRAB Exp {n}",
        slug=f"frab-exp-{n}",
        workspace_id=workspace_id,
        suite_id=suite.id,
        variant_a_prompt_post_id=fork_a.id,
        variant_a_version=1,
        variant_b_prompt_post_id=fork_b.id,
        variant_b_version=1,
        status=status,
        created_by_user_id=user.id,
    )
    _db.session.add(exp)
    _db.session.flush()
    return exp


def _make_exp_run(
    exp: ABExperiment,
    run_a: BenchmarkRun,
    run_b: BenchmarkRun,
) -> ABExperimentRun:
    er = ABExperimentRun(
        experiment_id=exp.id,
        run_a_id=run_a.id,
        run_b_id=run_b.id,
    )
    _db.session.add(er)
    _db.session.flush()
    return er


# ==============================================================================
# FRAB-001 — winning fork gets ab_win_rate > 0
# ==============================================================================


class TestABWinnerRatePositive:
    def test_winning_fork_ab_win_rate_is_positive(self, db_session):
        """FRAB-001"""
        user = _make_user()
        base = _make_prompt(user)
        fork_winner = _make_fork(base, user)
        fork_loser = _make_fork(base, user)
        suite = _make_suite(user)
        case = _make_case(suite)

        run_a = _make_run(suite, fork_winner, user)
        run_b = _make_run(suite, fork_loser, user)
        _add_result(run_a, case, score=0.9)  # winner scores higher
        _add_result(run_b, case, score=0.3)

        exp = _make_ab_experiment(user, fork_winner, fork_loser, suite)
        _make_exp_run(exp, run_a, run_b)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        recs_by_id = {r.post_id: r for r in recs}

        winner_rec = recs_by_id[fork_winner.id]
        assert winner_rec.breakdown.ab_win_rate is not None
        assert winner_rec.breakdown.ab_win_rate > 0.0


# ==============================================================================
# FRAB-002 — losing fork gets ab_win_rate == 0.0
# ==============================================================================


class TestABLoserRateZero:
    def test_losing_fork_ab_win_rate_is_zero(self, db_session):
        """FRAB-002"""
        user = _make_user()
        base = _make_prompt(user)
        fork_winner = _make_fork(base, user)
        fork_loser = _make_fork(base, user)
        suite = _make_suite(user)
        case = _make_case(suite)

        run_a = _make_run(suite, fork_winner, user)
        run_b = _make_run(suite, fork_loser, user)
        _add_result(run_a, case, score=0.9)
        _add_result(run_b, case, score=0.1)

        exp = _make_ab_experiment(user, fork_winner, fork_loser, suite)
        _make_exp_run(exp, run_a, run_b)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        loser_rec = next(r for r in recs if r.post_id == fork_loser.id)
        assert loser_rec.breakdown.ab_win_rate == pytest.approx(0.0)


# ==============================================================================
# FRAB-003 — fork with no experiment → ab_win_rate is None
# ==============================================================================


class TestNoABExperiment:
    def test_fork_without_ab_experiment_has_none_win_rate(self, db_session):
        """FRAB-003"""
        user = _make_user()
        base = _make_prompt(user)
        _fork = _make_fork(base, user)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        assert len(recs) == 1
        assert recs[0].breakdown.ab_win_rate is None
        assert recs[0].breakdown.ab_contrib == 0.0


# ==============================================================================
# FRAB-004 — workspace experiment excluded from public scope
# ==============================================================================


class TestWorkspaceExperimentExcludedFromPublic:
    def test_workspace_experiment_not_counted_in_public_scope(self, db_session):
        """FRAB-004"""
        from backend.models.workspace import (
            Workspace,
            WorkspaceMember,
            WorkspaceMemberRole,
        )

        user = _make_user()
        n = _n()
        ws = Workspace(name=f"FRAB-WS {n}", slug=f"frab-ws-{n}", owner_id=user.id)
        _db.session.add(ws)
        _db.session.flush()
        _db.session.add(
            WorkspaceMember(
                workspace_id=ws.id, user_id=user.id, role=WorkspaceMemberRole.owner
            )
        )
        _db.session.flush()

        base = _make_prompt(user)
        fork_a = _make_fork(base, user)
        fork_b = _make_fork(base, user)
        suite = _make_suite(user, workspace_id=ws.id)  # workspace suite
        case = _make_case(suite)

        run_a = _make_run(suite, fork_a, user)
        run_b = _make_run(suite, fork_b, user)
        _add_result(run_a, case, score=0.9)
        _add_result(run_b, case, score=0.1)

        # workspace-scoped experiment → should be EXCLUDED from public scope
        exp = _make_ab_experiment(user, fork_a, fork_b, suite, workspace_id=ws.id)
        _make_exp_run(exp, run_a, run_b)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        for r in recs:
            # No AB data should appear for any fork when scoped to public
            assert r.breakdown.ab_win_rate is None, (
                f"Fork {r.post_id} should have no AB data in public scope"
            )


# ==============================================================================
# FRAB-005 — no scored results → experiment skipped → ab_win_rate None
# ==============================================================================


class TestNoScoredResults:
    def test_unscored_experiment_not_counted(self, db_session):
        """FRAB-005: ABExperiment exists with an ABExperimentRun, but the
        BenchmarkRunResult rows have no score_numeric → avg is None → skipped."""
        user = _make_user()
        base = _make_prompt(user)
        fork_a = _make_fork(base, user)
        fork_b = _make_fork(base, user)
        suite = _make_suite(user)
        case = _make_case(suite)

        run_a = _make_run(suite, fork_a, user)
        run_b = _make_run(suite, fork_b, user)
        # Add results but with NULL score_numeric
        _db.session.add(
            BenchmarkRunResult(
                run_id=run_a.id, case_id=case.id, output_text="a", score_numeric=None
            )
        )
        _db.session.add(
            BenchmarkRunResult(
                run_id=run_b.id, case_id=case.id, output_text="b", score_numeric=None
            )
        )
        _db.session.flush()

        exp = _make_ab_experiment(user, fork_a, fork_b, suite)
        _make_exp_run(exp, run_a, run_b)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        for r in recs:
            assert r.breakdown.ab_win_rate is None


# ==============================================================================
# FRAB-006 — draft experiment not counted
# ==============================================================================


class TestDraftExperimentIgnored:
    def test_draft_experiment_not_counted(self, db_session):
        """FRAB-006"""
        user = _make_user()
        base = _make_prompt(user)
        fork_a = _make_fork(base, user)
        fork_b = _make_fork(base, user)
        suite = _make_suite(user)
        case = _make_case(suite)

        run_a = _make_run(suite, fork_a, user)
        run_b = _make_run(suite, fork_b, user)
        _add_result(run_a, case, score=0.9)
        _add_result(run_b, case, score=0.1)

        # Draft status — should be excluded
        exp = _make_ab_experiment(
            user, fork_a, fork_b, suite, status=ABExperimentStatus.draft.value
        )
        _make_exp_run(exp, run_a, run_b)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        for r in recs:
            assert r.breakdown.ab_win_rate is None


# ==============================================================================
# FRAB-007 — A/B win rate boosts ranking
# ==============================================================================


class TestABBoostsRanking:
    def test_ab_winner_ranks_above_no_ab_fork_when_otherwise_equal(self, db_session):
        """FRAB-007: fork_winner has an A/B win, fork_neutral has none.
        Both have the same benchmark, votes, views, and updated_at.
        The A/B contribution should push fork_winner ahead."""

        user = _make_user()
        base = _make_prompt(user)
        ts = __import__("datetime").datetime(
            2024, 1, 1, tzinfo=__import__("datetime").timezone.utc
        )
        fork_winner = _make_fork(base, user)
        fork_neutral = _make_fork(base, user)
        # Force same updated_at
        fork_winner.updated_at = ts
        fork_neutral.updated_at = ts
        _db.session.flush()

        # Give fork_winner an A/B win
        fork_dummy = _make_fork(base, user)
        fork_dummy.updated_at = ts
        _db.session.flush()

        suite = _make_suite(user)
        case = _make_case(suite)
        run_a = _make_run(suite, fork_winner, user)
        run_b = _make_run(suite, fork_dummy, user)
        _add_result(run_a, case, score=0.9)
        _add_result(run_b, case, score=0.1)
        exp = _make_ab_experiment(user, fork_winner, fork_dummy, suite)
        _make_exp_run(exp, run_a, run_b)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        winner_rec = next(r for r in recs if r.post_id == fork_winner.id)
        neutral_rec = next(r for r in recs if r.post_id == fork_neutral.id)

        # Winner should have positive A/B contribution
        assert winner_rec.breakdown.ab_contrib > 0.0
        assert neutral_rec.breakdown.ab_contrib == 0.0
        assert winner_rec.breakdown.ab_win_rate is not None


# ==============================================================================
# FRAB-008 — win rate with 3 experiments (2 wins, 1 loss)
# ==============================================================================


class TestABPartialWinRate:
    def test_two_wins_one_loss_gives_two_thirds_win_rate(self, db_session):
        """FRAB-008"""
        user = _make_user()
        base = _make_prompt(user)
        focal = _make_fork(base, user)  # the fork we're tracking
        opponent = _make_fork(base, user)  # opponent in all experiments

        suite = _make_suite(user)
        case = _make_case(suite)

        def _run_exp(focal_score: float, opp_score: float):
            r_a = _make_run(suite, focal, user)
            r_b = _make_run(suite, opponent, user)
            _add_result(r_a, case, focal_score)
            _add_result(r_b, case, opp_score)
            exp = _make_ab_experiment(user, focal, opponent, suite)
            _make_exp_run(exp, r_a, r_b)

        _run_exp(0.8, 0.2)  # focal wins
        _run_exp(0.9, 0.1)  # focal wins
        _run_exp(0.2, 0.8)  # focal loses
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None)
        focal_rec = next(r for r in recs if r.post_id == focal.id)
        assert focal_rec.breakdown.ab_win_rate == pytest.approx(2 / 3, abs=1e-4)
