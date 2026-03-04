"""Tests for user_analytics_service.build_ontology_contributions.

Coverage
--------
  ON-001  No ontology mappings: returns empty list.
  ON-002  Single post mapped to a node: returns that node with count 1.
  ON-003  Result is bounded to top 5 by default.
  ON-004  Ordered by count desc, then node_id desc for deterministic tie-breaking.
  ON-005  Draft posts excluded; only published posts count.
  ON-006  Custom limit parameter is respected.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.services.user_analytics_service import build_ontology_contributions

_ctr = itertools.count(9000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"on{n}@example.com",
        username=f"onuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_post(author, *, status=PostStatus.published, workspace_id=None):
    n = _n()
    p = Post(
        title=f"ON-Post {n}",
        slug=f"on-post-{n}",
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


def _make_node(admin_user, *, name: str | None = None):
    n = _n()
    node = OntologyNode(
        slug=f"on-node-{n}",
        name=name or f"ON-Node {n}",
        is_public=True,
        created_by_user_id=admin_user.id,
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _map(post, node, *, workspace_id=None, created_by=None):
    mapping = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=workspace_id,
        created_by_user_id=created_by or post.author_id,
    )
    _db.session.add(mapping)
    _db.session.flush()
    return mapping


# ── ON-001 ─────────────────────────────────────────────────────────────────────


class TestOntologyEmpty:
    def test_empty_returns_empty_list(self, db_session):
        user = _make_user()
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert result == []


# ── ON-002 ─────────────────────────────────────────────────────────────────────


class TestOntologySingleMapping:
    def test_single_post_mapped_to_node_count_1(self, db_session):
        user = _make_user()
        post = _make_post(user)
        node = _make_node(user, name="AI Prompts")
        _map(post, node)
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert len(result) == 1
        assert result[0]["node"].id == node.id
        assert result[0]["count"] == 1


# ── ON-003 ─────────────────────────────────────────────────────────────────────


class TestOntologyBoundedToFive:
    def test_returns_at_most_five_nodes(self, db_session):
        user = _make_user()
        nodes = [_make_node(user) for _ in range(7)]
        for node in nodes:
            post = _make_post(user)
            _map(post, node)
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert len(result) <= 5


# ── ON-004 ─────────────────────────────────────────────────────────────────────


class TestOntologyOrdering:
    def test_higher_count_ranks_first(self, db_session):
        user = _make_user()
        node_low = _make_node(user, name="Low")
        node_high = _make_node(user, name="High")

        # 1 post → node_low
        post_l = _make_post(user)
        _map(post_l, node_low)

        # 3 posts → node_high
        for _ in range(3):
            post_h = _make_post(user)
            _map(post_h, node_high)

        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert len(result) == 2
        assert result[0]["node"].id == node_high.id
        assert result[0]["count"] == 3
        assert result[1]["node"].id == node_low.id
        assert result[1]["count"] == 1

    def test_tie_break_is_node_id_desc(self, db_session):
        """When two nodes have equal count, the higher node_id appears first."""
        user = _make_user()
        node_a = _make_node(user, name="TieA")
        node_b = _make_node(user, name="TieB")
        # node_b was inserted after node_a so node_b.id > node_a.id

        post1 = _make_post(user)
        post2 = _make_post(user)
        _map(post1, node_a)
        _map(post2, node_b)
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert len(result) == 2
        # Both have count 1; node_b should rank first (higher id)
        assert result[0]["node"].id == node_b.id
        assert result[1]["node"].id == node_a.id


# ── ON-005 ─────────────────────────────────────────────────────────────────────


class TestOntologyDraftExcluded:
    def test_draft_post_not_counted(self, db_session):
        user = _make_user()
        node = _make_node(user)
        draft_post = _make_post(user, status=PostStatus.draft)
        _map(draft_post, node)
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert result == []

    def test_only_published_counted(self, db_session):
        user = _make_user()
        node = _make_node(user)
        _make_post(
            user, status=PostStatus.draft
        )  # unmapped but just to ensure the user exists
        pub = _make_post(user)
        _map(pub, node)
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert len(result) == 1
        assert result[0]["count"] == 1


# ── ON-006 ─────────────────────────────────────────────────────────────────────


class TestOntologyCustomLimit:
    def test_custom_limit_respected(self, db_session):
        user = _make_user()
        for _ in range(5):
            node = _make_node(user)
            post = _make_post(user)
            _map(post, node)
        _db.session.commit()

        result_3 = build_ontology_contributions(user.id, public_only=True, limit=3)
        result_all = build_ontology_contributions(user.id, public_only=True, limit=10)

        assert len(result_3) == 3
        assert len(result_all) == 5
