"""Tests verifying determinism and tie-breaking for ContributorCardService."""

from __future__ import annotations

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.contributor_card_service import (
    ContributorCardService,
    _build_ranked_cards,
    _normalize,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _post(author, *, slug_suffix="", workspace_id=None):
    slug = f"det-post-{author.id}-{slug_suffix}"
    p = Post(
        author_id=author.id,
        title=f"Post {slug}",
        slug=slug,
        markdown_body="body",
        status=PostStatus.published,
        version=1,
        workspace_id=workspace_id,
    )
    db.session.add(p)
    db.session.flush()
    return p


def _accepted_revision(post, contributor, reviewer):
    rev = Revision(
        post_id=post.id,
        author_id=contributor.id,
        base_version_number=1,
        proposed_markdown="improved",
        summary="fix",
        status=RevisionStatus.accepted,
        reviewed_by_id=reviewer.id,
    )
    db.session.add(rev)
    db.session.flush()
    return rev


# ── pure-unit helpers ─────────────────────────────────────────────────────────


class TestNormalize:
    def test_empty_returns_empty(self):
        assert _normalize({}) == {}

    def test_all_zeros(self):
        result = _normalize({1: 0.0, 2: 0.0, 3: 0.0})
        assert all(v == 0.0 for v in result.values())

    def test_max_becomes_one(self):
        result = _normalize({1: 0.0, 2: 5.0, 3: 10.0})
        assert result[3] == 1.0

    def test_min_becomes_zero(self):
        result = _normalize({1: 0.0, 2: 5.0, 3: 10.0})
        assert result[1] == 0.0

    def test_single_nonzero_value(self):
        result = _normalize({7: 42.0})
        assert result[7] == 1.0


class TestBuildRankedCards:
    def test_empty_metrics_returns_empty(self):
        result = _build_ranked_cards({}, limit=10, include_badges=False)
        assert result == []

    def test_tie_break_higher_user_id_first(self):
        """Equal revision counts → user_id DESC tie-break."""
        metrics = {
            101: {
                "username": "ua",
                "display_name": None,
                "avatar_url": None,
                "accepted_revisions": 3,
                "benchmark_improvements": 0,
                "ab_wins": 0,
                "ontology_breadth": 0,
            },
            102: {
                "username": "ub",
                "display_name": None,
                "avatar_url": None,
                "accepted_revisions": 3,
                "benchmark_improvements": 0,
                "ab_wins": 0,
                "ontology_breadth": 0,
            },
        }
        cards = _build_ranked_cards(metrics, limit=10, include_badges=False)
        # Both have same score → user_id DESC → uid 102 first
        assert cards[0].user_id == 102
        assert cards[1].user_id == 101

    def test_rank_sequence_is_sequential(self):
        metrics = {
            1: {
                "username": "a",
                "display_name": None,
                "avatar_url": None,
                "accepted_revisions": 5,
                "benchmark_improvements": 0,
                "ab_wins": 0,
                "ontology_breadth": 0,
            },
            2: {
                "username": "b",
                "display_name": None,
                "avatar_url": None,
                "accepted_revisions": 2,
                "benchmark_improvements": 0,
                "ab_wins": 0,
                "ontology_breadth": 0,
            },
        }
        cards = _build_ranked_cards(metrics, limit=10, include_badges=False)
        assert [c.rank for c in cards] == [1, 2]

    def test_improver_score_is_deterministic(self):
        metrics = {
            10: {
                "username": "x",
                "display_name": None,
                "avatar_url": None,
                "accepted_revisions": 7,
                "benchmark_improvements": 0,
                "ab_wins": 2,
                "ontology_breadth": 3,
            },
        }
        cards_a = _build_ranked_cards(metrics, limit=10, include_badges=False)
        cards_b = _build_ranked_cards(metrics, limit=10, include_badges=False)
        assert cards_a[0].improver_score == cards_b[0].improver_score

    def test_limit_applied(self):
        metrics = {
            i: {
                "username": f"u{i}",
                "display_name": None,
                "avatar_url": None,
                "accepted_revisions": i,
                "benchmark_improvements": 0,
                "ab_wins": 0,
                "ontology_breadth": 0,
            }
            for i in range(1, 11)
        }
        cards = _build_ranked_cards(metrics, limit=5, include_badges=False)
        assert len(cards) == 5


class TestGlobalDeterminism:
    def test_same_input_gives_same_output(self, db_session, make_user_token):
        author, _ = make_user_token("det_auth@example.com", "det_auth")
        editor, _ = make_user_token("det_ed@example.com", "det_ed", role="editor")
        contrib, _ = make_user_token("det_c@example.com", "det_c", role="contributor")

        p = _post(author, slug_suffix="det1")
        _accepted_revision(p, contrib, editor)
        db.session.commit()

        cards_a = ContributorCardService.get_top_improvers_global()
        cards_b = ContributorCardService.get_top_improvers_global()

        assert [c.user_id for c in cards_a] == [c.user_id for c in cards_b]
        assert [c.improver_score for c in cards_a] == [
            c.improver_score for c in cards_b
        ]

    def test_tie_break_consistent_across_calls(self, db_session, make_user_token):
        editor, _ = make_user_token("det_tbed@example.com", "det_tbed", role="editor")
        # Two contributors each with exactly 1 accepted revision → tied score
        users = []
        for i in range(2):
            u, _ = make_user_token(
                f"det_tb{i}@example.com", f"det_tb{i}", role="contributor"
            )
            users.append(u)
            p = _post(u, slug_suffix=f"tb{i}")
            _accepted_revision(p, u, editor)
        db.session.commit()

        results = [
            [c.user_id for c in ContributorCardService.get_top_improvers_global()]
            for _ in range(3)
        ]
        # All three calls must return same order
        assert results[0] == results[1] == results[2]
        # Higher user_id must be first (tie-break: user_id DESC)
        first_ids = results[0]
        assert first_ids[0] > first_ids[1]
