"""Tests verifying scope isolation for ContributorCardService."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace
from backend.services.contributor_card_service import ContributorCardService

# ── helpers ───────────────────────────────────────────────────────────────────


def _ws(owner, slug):
    w = Workspace(slug=slug, name=f"WS {slug}", owner_id=owner.id)
    db.session.add(w)
    db.session.flush()
    return w


def _post(author, *, slug_suffix="", workspace_id=None):
    slug = f"si-post-{author.id}-{slug_suffix}"
    p = Post(
        author_id=author.id,
        title=f"SI Post {slug}",
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


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def owner(make_user_token):
    u, _ = make_user_token("si_owner@example.com", "si_owner", role="editor")
    return u


@pytest.fixture()
def public_user(make_user_token):
    u, _ = make_user_token("si_pub@example.com", "si_pub", role="contributor")
    return u


@pytest.fixture()
def ws_user(make_user_token):
    u, _ = make_user_token("si_ws@example.com", "si_ws", role="contributor")
    return u


# ── tests ─────────────────────────────────────────────────────────────────────


class TestGlobalScopeIsolation:
    def test_workspace_user_absent_in_global_public(self, db_session, owner, ws_user):
        ws = _ws(owner, slug="si-g-ws")
        ws_post = _post(ws_user, slug_suffix="gws", workspace_id=ws.id)
        _accepted_revision(ws_post, ws_user, owner)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_global()
        assert all(c.user_id != ws_user.id for c in cards)

    def test_public_user_present_in_global_public(self, db_session, owner, public_user):
        pub_post = _post(public_user, slug_suffix="gpub")
        _accepted_revision(pub_post, public_user, owner)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_global()
        assert any(c.user_id == public_user.id for c in cards)


class TestPromptScopeIsolation:
    def test_workspace_revision_invisible_in_public_prompt_scope(
        self, db_session, owner, ws_user
    ):
        ws = _ws(owner, slug="si-p-ws")
        ws_prompt = _post(owner, slug_suffix="sp-base")
        ws_post = _post(ws_user, slug_suffix="sp-ws", workspace_id=ws.id)
        _accepted_revision(ws_post, ws_user, owner)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            ws_prompt, workspace=None
        )
        assert all(c.user_id != ws_user.id for c in cards)

    def test_workspace_revision_visible_in_matching_ws_prompt_scope(
        self, db_session, owner, ws_user
    ):
        ws = _ws(owner, slug="si-pw-ws")
        ws_prompt = _post(owner, slug_suffix="spw-base", workspace_id=ws.id)
        _accepted_revision(ws_prompt, ws_user, owner)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            ws_prompt, workspace=ws
        )
        assert any(c.user_id == ws_user.id for c in cards)

    def test_workspace_a_contributor_invisible_in_workspace_b(
        self, db_session, owner, ws_user
    ):
        ws_a = _ws(owner, slug="si-wp-a")
        ws_b = _ws(owner, slug="si-wp-b")

        prompt_a = _post(owner, slug_suffix="spa", workspace_id=ws_a.id)
        _accepted_revision(prompt_a, ws_user, owner)
        # ws_b has no its own prompt; use the origin (public)
        origin_pub = _post(owner, slug_suffix="spa-origin")
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_prompt(
            origin_pub, workspace=ws_b
        )
        # ws_user's revision is on ws_a prompt, not on origin_pub family
        assert all(c.user_id != ws_user.id for c in cards)
