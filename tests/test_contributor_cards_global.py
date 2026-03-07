"""Tests for ContributorCardService.get_top_improvers_global."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.contributor_card_service import (
    ContributorCard,
    ContributorCardService,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _post(author, *, slug_suffix="", workspace_id=None):
    slug = f"post-{author.id}-{slug_suffix}"
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
        proposed_markdown="improved body",
        summary="improvement",
        status=RevisionStatus.accepted,
        reviewed_by_id=reviewer.id,
    )
    db.session.add(rev)
    db.session.flush()
    return rev


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    u, _ = make_user_token("gc_author@example.com", "gc_author")
    return u


@pytest.fixture()
def contrib_a(make_user_token):
    u, _ = make_user_token("gc_a@example.com", "gc_a", role="contributor")
    return u


@pytest.fixture()
def contrib_b(make_user_token):
    u, _ = make_user_token("gc_b@example.com", "gc_b", role="contributor")
    return u


@pytest.fixture()
def editor(make_user_token):
    u, _ = make_user_token("gc_editor@example.com", "gc_editor", role="editor")
    return u


# ── tests ─────────────────────────────────────────────────────────────────────


class TestGetTopImprovGlobal:
    def test_returns_empty_when_no_revisions(self, db_session):
        result = ContributorCardService.get_top_improvers_global()
        assert result == []

    def test_returns_contributor_card_instances(
        self, db_session, author, contrib_a, editor
    ):
        p = _post(author, slug_suffix="1")
        _accepted_revision(p, contrib_a, editor)
        db.session.commit()

        result = ContributorCardService.get_top_improvers_global()

        assert len(result) == 1
        assert isinstance(result[0], ContributorCard)

    def test_card_fields_populated(self, db_session, author, contrib_a, editor):
        p = _post(author, slug_suffix="f")
        _accepted_revision(p, contrib_a, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_global()

        card = cards[0]
        assert card.user_id == contrib_a.id
        assert card.username == contrib_a.username
        assert card.accepted_revisions == 1
        assert card.rank == 1
        assert isinstance(card.improver_score, float)
        assert isinstance(card.badge_keys, list)

    def test_excludes_workspace_revisions(
        self, db_session, author, contrib_a, editor, make_user_token
    ):
        # workspace-scoped post — revision on it should not count for global
        ws_owner, _ = make_user_token("gc_wsowner@example.com", "gc_wsowner")
        from backend.models.workspace import Workspace

        ws = Workspace(slug="gc-test-ws", name="GC WS", owner_id=ws_owner.id)
        db.session.add(ws)
        db.session.flush()

        p = _post(author, slug_suffix="ws", workspace_id=ws.id)
        _accepted_revision(p, contrib_a, editor)
        db.session.commit()

        result = ContributorCardService.get_top_improvers_global()
        assert result == []

    def test_higher_revision_count_ranks_first(
        self, db_session, author, contrib_a, contrib_b, editor
    ):
        p1 = _post(author, slug_suffix="r1")
        p2 = _post(author, slug_suffix="r2")
        p3 = _post(author, slug_suffix="r3")

        _accepted_revision(p1, contrib_a, editor)
        _accepted_revision(p2, contrib_a, editor)
        _accepted_revision(p3, contrib_b, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_global()

        assert cards[0].user_id == contrib_a.id
        assert cards[0].accepted_revisions == 2
        assert cards[1].user_id == contrib_b.id
        assert cards[1].accepted_revisions == 1

    def test_limit_respected(self, db_session, author, make_user_token, editor):
        contribs = []
        for i in range(5):
            u, _ = make_user_token(
                f"gc_lim{i}@example.com", f"gc_lim{i}", role="contributor"
            )
            contribs.append(u)

        for idx, c in enumerate(contribs):
            p = _post(author, slug_suffix=f"lim{idx}")
            _accepted_revision(p, c, editor)
        db.session.commit()

        result = ContributorCardService.get_top_improvers_global(limit=3)
        assert len(result) == 3

    def test_rank_sequence_starts_at_one(
        self, db_session, author, contrib_a, contrib_b, editor
    ):
        p1 = _post(author, slug_suffix="rs1")
        p2 = _post(author, slug_suffix="rs2")
        _accepted_revision(p1, contrib_a, editor)
        _accepted_revision(p2, contrib_b, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_global()
        ranks = [c.rank for c in cards]
        assert ranks == sorted(ranks)
        assert ranks[0] == 1

    def test_improver_score_between_zero_and_one(
        self, db_session, author, contrib_a, editor
    ):
        p = _post(author, slug_suffix="sc")
        _accepted_revision(p, contrib_a, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_global()
        assert all(0.0 <= c.improver_score <= 1.0 for c in cards)
