"""Tests for ContributorCardService.get_top_improvers_for_prompt (prompt family)."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.contributor_card_service import ContributorCardService

# ── helpers ───────────────────────────────────────────────────────────────────


def _prompt(author, *, slug_suffix="", workspace_id=None):
    slug = f"pf-prompt-{author.id}-{slug_suffix}"
    p = Post(
        author_id=author.id,
        title=f"Prompt {slug}",
        slug=slug,
        markdown_body="prompt body",
        status=PostStatus.published,
        version=1,
        workspace_id=workspace_id,
    )
    db.session.add(p)
    db.session.flush()
    return p


def _fork(origin, author, creator, *, slug_suffix="fork", workspace_id=None):
    """Create a fork post + derived_from ContentLink."""
    fork_post = _prompt(author, slug_suffix=slug_suffix, workspace_id=workspace_id)
    cl = ContentLink(
        from_post_id=fork_post.id,
        to_post_id=origin.id,
        link_type="derived_from",
        workspace_id=workspace_id,
        created_by_user_id=creator.id,
    )
    db.session.add(cl)
    db.session.flush()
    return fork_post


def _accepted_revision(post, contributor, reviewer):
    rev = Revision(
        post_id=post.id,
        author_id=contributor.id,
        base_version_number=1,
        proposed_markdown="improved prompt",
        summary="prompt fix",
        status=RevisionStatus.accepted,
        reviewed_by_id=reviewer.id,
    )
    db.session.add(rev)
    db.session.flush()
    return rev


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def author(make_user_token):
    u, _ = make_user_token("pf_author@example.com", "pf_author")
    return u


@pytest.fixture()
def contrib_a(make_user_token):
    u, _ = make_user_token("pf_a@example.com", "pf_a", role="contributor")
    return u


@pytest.fixture()
def contrib_b(make_user_token):
    u, _ = make_user_token("pf_b@example.com", "pf_b", role="contributor")
    return u


@pytest.fixture()
def editor(make_user_token):
    u, _ = make_user_token("pf_ed@example.com", "pf_ed", role="editor")
    return u


# ── tests ─────────────────────────────────────────────────────────────────────


class TestPromptFamily:
    def test_no_revisions_returns_empty(self, db_session, author):
        prompt = _prompt(author, slug_suffix="empty")
        db.session.commit()

        result = ContributorCardService.get_top_improvers_for_prompt(
            prompt, workspace=None
        )
        assert result == []

    def test_finds_contributor_on_origin(self, db_session, author, contrib_a, editor):
        prompt = _prompt(author, slug_suffix="origin")
        _accepted_revision(prompt, contrib_a, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            prompt, workspace=None
        )
        assert len(cards) == 1
        assert cards[0].user_id == contrib_a.id

    def test_finds_contributor_on_fork(
        self, db_session, author, contrib_a, contrib_b, editor
    ):
        prompt = _prompt(author, slug_suffix="fk-origin")
        fork = _fork(prompt, author, editor, slug_suffix="fork-fk")
        _accepted_revision(fork, contrib_b, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            prompt, workspace=None
        )
        user_ids = {c.user_id for c in cards}
        assert contrib_b.id in user_ids

    def test_includes_both_origin_and_fork_contributors(
        self, db_session, author, contrib_a, contrib_b, editor
    ):
        prompt = _prompt(author, slug_suffix="both")
        fork = _fork(prompt, author, editor, slug_suffix="both-fork")
        _accepted_revision(prompt, contrib_a, editor)
        _accepted_revision(fork, contrib_b, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            prompt, workspace=None
        )
        user_ids = {c.user_id for c in cards}
        assert contrib_a.id in user_ids
        assert contrib_b.id in user_ids

    def test_more_revisions_ranks_higher(
        self, db_session, author, contrib_a, contrib_b, editor
    ):
        prompt = _prompt(author, slug_suffix="rank")
        fork = _fork(prompt, author, editor, slug_suffix="rank-fork")
        # contrib_a gets a revision on both origin and fork (2 in family), contrib_b gets 1
        _accepted_revision(prompt, contrib_a, editor)
        _accepted_revision(fork, contrib_a, editor)
        _accepted_revision(prompt, contrib_b, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            prompt, workspace=None
        )
        assert cards[0].user_id == contrib_a.id

    def test_limit_respected(self, db_session, author, editor, make_user_token):
        prompt = _prompt(author, slug_suffix="lim")
        for i in range(5):
            c, _ = make_user_token(
                f"pf_lm{i}@example.com", f"pf_lm{i}", role="contributor"
            )
            _accepted_revision(prompt, c, editor)
        db.session.commit()

        result = ContributorCardService.get_top_improvers_for_prompt(
            prompt, workspace=None, limit=3
        )
        assert len(result) == 3

    def test_does_not_include_unrelated_revision(
        self, db_session, author, contrib_a, contrib_b, editor
    ):
        prompt = _prompt(author, slug_suffix="unrela")
        other_prompt = _prompt(author, slug_suffix="unrela-other")
        _accepted_revision(prompt, contrib_a, editor)
        # contrib_b only has revision on unrelated post
        _accepted_revision(other_prompt, contrib_b, editor)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            prompt, workspace=None
        )
        user_ids = {c.user_id for c in cards}
        assert contrib_a.id in user_ids
        assert contrib_b.id not in user_ids
