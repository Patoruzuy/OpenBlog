"""Tests for ContributorCardService.get_top_improvers_for_ontology (public scope)."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.services.contributor_card_service import ContributorCardService

# ── helpers ───────────────────────────────────────────────────────────────────


def _node(creator, *, slug="concept-pub", name="Concept Pub", parent=None):
    n = OntologyNode(
        slug=slug,
        name=name,
        created_by_user_id=creator.id,
        is_public=True,
        parent_id=parent.id if parent else None,
    )
    db.session.add(n)
    db.session.flush()
    return n


def _post(author, *, slug_suffix=""):
    slug = f"op-post-{author.id}-{slug_suffix}"
    p = Post(
        author_id=author.id,
        title=f"Post {slug}",
        slug=slug,
        markdown_body="body",
        status=PostStatus.published,
        version=1,
    )
    db.session.add(p)
    db.session.flush()
    return p


def _map_post(post, node, creator):
    co = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=None,
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
def creator(make_user_token):
    u, _ = make_user_token("op_creator@example.com", "op_creator", role="editor")
    return u


@pytest.fixture()
def contrib_a(make_user_token):
    u, _ = make_user_token("op_a@example.com", "op_a", role="contributor")
    return u


@pytest.fixture()
def contrib_b(make_user_token):
    u, _ = make_user_token("op_b@example.com", "op_b", role="contributor")
    return u


# ── tests ─────────────────────────────────────────────────────────────────────


class TestOntologyPublic:
    def test_returns_empty_for_unmapped_node(self, db_session, creator):
        node = _node(creator)
        db.session.commit()

        result = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=None
        )
        assert result == []

    def test_finds_contributor_through_mapped_post(
        self, db_session, creator, contrib_a
    ):
        node = _node(creator, slug="op-n1")
        post = _post(contrib_a, slug_suffix="n1")
        _map_post(post, node, creator)
        _accepted_revision(post, contrib_a, creator)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=None
        )
        assert len(cards) == 1
        assert cards[0].user_id == contrib_a.id

    def test_includes_descendants(self, db_session, creator, contrib_a):
        parent = _node(creator, slug="op-parent", name="Parent")
        child = _node(creator, slug="op-child", name="Child", parent=parent)
        post = _post(contrib_a, slug_suffix="desc")
        _map_post(post, child, creator)
        _accepted_revision(post, contrib_a, creator)
        db.session.commit()

        # Query on parent should find contributor via child node
        cards = ContributorCardService.get_top_improvers_for_ontology(
            parent, workspace=None
        )
        assert any(c.user_id == contrib_a.id for c in cards)

    def test_orders_by_revision_count(self, db_session, creator, contrib_a, contrib_b):
        node = _node(creator, slug="op-ord")
        p1 = _post(contrib_a, slug_suffix="ord1")
        p2 = _post(contrib_a, slug_suffix="ord2")
        p3 = _post(contrib_b, slug_suffix="ord3")
        _map_post(p1, node, creator)
        _map_post(p2, node, creator)
        _map_post(p3, node, creator)
        _accepted_revision(p1, contrib_a, creator)
        _accepted_revision(p2, contrib_a, creator)
        _accepted_revision(p3, contrib_b, creator)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=None
        )
        assert cards[0].user_id == contrib_a.id
        assert cards[1].user_id == contrib_b.id

    def test_limit_respected(self, db_session, creator, make_user_token):
        node = _node(creator, slug="op-lim")
        for i in range(5):
            c, _ = make_user_token(
                f"op_lm{i}@example.com", f"op_lm{i}", role="contributor"
            )
            p = _post(c, slug_suffix=f"lm{i}")
            _map_post(p, node, creator)
            _accepted_revision(p, c, creator)
        db.session.commit()

        result = ContributorCardService.get_top_improvers_for_ontology(
            node, workspace=None, limit=3
        )
        assert len(result) == 3

    def test_ontology_breadth_counts_distinct_nodes(
        self, db_session, creator, contrib_a
    ):
        n1 = _node(creator, slug="op-bn1", name="BN1")
        n2 = _node(creator, slug="op-bn2", name="BN2")
        p1 = _post(contrib_a, slug_suffix="b1")
        p2 = _post(contrib_a, slug_suffix="b2")
        _map_post(p1, n1, creator)
        _map_post(p2, n2, creator)
        _accepted_revision(p1, contrib_a, creator)
        _accepted_revision(p2, contrib_a, creator)
        db.session.commit()

        cards = ContributorCardService.get_top_improvers_for_ontology(
            n1, workspace=None
        )
        assert len(cards) >= 1
        card = next(c for c in cards if c.user_id == contrib_a.id)
        assert card.ontology_breadth >= 1
