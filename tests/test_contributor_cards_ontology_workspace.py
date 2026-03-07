"""Tests for ContributorCardService.get_top_improvers_for_ontology (workspace scope)."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace
from backend.services.contributor_card_service import ContributorCardService

# ── helpers ───────────────────────────────────────────────────────────────────


def _ws(owner, *, slug="ow-ws"):
    w = Workspace(slug=slug, name=f"WS {slug}", owner_id=owner.id)
    db.session.add(w)
    db.session.flush()
    return w


def _node(creator, *, slug="ow-concept", name="OW Concept"):
    n = OntologyNode(
        slug=slug,
        name=name,
        created_by_user_id=creator.id,
        is_public=True,
    )
    db.session.add(n)
    db.session.flush()
    return n


def _post(author, *, slug_suffix="", workspace_id=None):
    slug = f"ow-p-{author.id}-{slug_suffix}"
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


def _map_post(post, node, creator, workspace_id=None):
    co = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=workspace_id,
        created_by_user_id=creator.id,
    )
    db.session.add(co)
    db.session.flush()
    return co


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
    u, _ = make_user_token("ow_owner@example.com", "ow_owner", role="editor")
    return u


@pytest.fixture()
def contrib_ws(make_user_token):
    u, _ = make_user_token("ow_ws@example.com", "ow_ws", role="contributor")
    return u


@pytest.fixture()
def contrib_pub(make_user_token):
    u, _ = make_user_token("ow_pub@example.com", "ow_pub", role="contributor")
    return u


# ── tests ─────────────────────────────────────────────────────────────────────


class TestOntologyWorkspace:
    def test_workspace_contributor_found_with_ws_scope(
        self, db_session, owner, contrib_ws
    ):
        ws = _ws(owner, slug="ow-ws-scope")
        node = _node(owner, slug="ow-ws-n1")
        post = _post(contrib_ws, slug_suffix="ws1", workspace_id=ws.id)
        _map_post(post, node, owner, workspace_id=ws.id)
        _accepted_revision(post, contrib_ws, owner)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=ws
        )
        assert any(c.user_id == contrib_ws.id for c in cards)

    def test_workspace_contributor_hidden_from_public_scope(
        self, db_session, owner, contrib_ws
    ):
        ws = _ws(owner, slug="ow-ws-hide")
        node = _node(owner, slug="ow-ws-n2")
        post = _post(contrib_ws, slug_suffix="ws2", workspace_id=ws.id)
        _map_post(post, node, owner, workspace_id=ws.id)
        _accepted_revision(post, contrib_ws, owner)
        db.session.commit()

        # Public scope must not see workspace contributor
        cards = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=None
        )
        assert all(c.user_id != contrib_ws.id for c in cards)

    def test_public_contributor_visible_in_workspace_scope(
        self, db_session, owner, contrib_pub
    ):
        ws = _ws(owner, slug="ow-ws-pub")
        node = _node(owner, slug="ow-ws-n3")
        # Public post mapped publicly
        post = _post(contrib_pub, slug_suffix="ws3", workspace_id=None)
        _map_post(post, node, owner, workspace_id=None)
        _accepted_revision(post, contrib_pub, owner)
        db.session.commit()

        # Workspace scope should include public contributor too
        cards = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=ws
        )
        assert any(c.user_id == contrib_pub.id for c in cards)

    def test_other_workspace_contributor_isolated(self, db_session, owner, contrib_ws):
        ws_a = _ws(owner, slug="ow-ws-A")
        ws_b = _ws(owner, slug="ow-ws-B")
        node = _node(owner, slug="ow-ws-iso")

        # contrib_ws only in ws_a
        post = _post(contrib_ws, slug_suffix="iso", workspace_id=ws_a.id)
        _map_post(post, node, owner, workspace_id=ws_a.id)
        _accepted_revision(post, contrib_ws, owner)
        db.session.commit()

        # ws_b scope must NOT see ws_a contributor
        cards = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=ws_b
        )
        assert all(c.user_id != contrib_ws.id for c in cards)
