"""Tests for user_analytics_service scope isolation.

Coverage
--------
  SC-001  Workspace post NOT counted in public_only heatmap.
  SC-002  Workspace post IS counted when public_only=False (owner view).
  SC-003  Another user's public post is NOT counted for the target user.
  SC-004  Workspace benchmark run NOT counted in public_only summary.
  SC-005  Workspace ontology mapping excluded from public_only view.
  SC-006  Revision on workspace post excluded from public_only heatmap.
  SC-007  Total DB round-trips for all 4 analytics functions is ≤ 8.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.ai_review import AIReviewRequest
from backend.models.benchmark import BenchmarkRun, BenchmarkSuite
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.user_analytics_service import (
    build_contribution_heatmap,
    build_ontology_contributions,
    build_user_contribution_summary,
    compute_contribution_streak,
)

_ctr = itertools.count(11000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"sc{n}@example.com",
        username=f"scuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"SC-WS {n}", slug=f"sc-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _make_post(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"SC-Post {n}",
        slug=f"sc-post-{n}",
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


def _make_revision(post, author, *, accepted=True):
    n = _n()
    r = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_number=1,
        proposed_markdown="rev",
        summary=f"SC-Rev {n}",
        status=RevisionStatus.accepted if accepted else RevisionStatus.pending,
        reviewed_at=datetime.now(UTC) if accepted else None,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"SC-Suite {n}",
        slug=f"sc-suite-{n}",
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


def _make_ai_review(post, user, *, workspace_id=None):
    n = _n()
    req = AIReviewRequest(
        workspace_id=workspace_id,
        post_id=post.id,
        requested_by_user_id=user.id,
        review_type="clarity",
        status="queued",
        input_fingerprint=f"fp-sc{n}",
        created_at=datetime.now(UTC),
    )
    _db.session.add(req)
    _db.session.flush()
    return req


def _make_node(user):
    n = _n()
    node = OntologyNode(
        slug=f"sc-node-{n}",
        name=f"SC-Node {n}",
        is_public=True,
        created_by_user_id=user.id,
    )
    _db.session.add(node)
    _db.session.flush()
    return node


def _map_ontology(post, node, *, workspace_id=None):
    m = ContentOntology(
        post_id=post.id,
        ontology_node_id=node.id,
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
    )
    _db.session.add(m)
    _db.session.flush()
    return m


# ── SC-001 ─────────────────────────────────────────────────────────────────────


class TestWorkspacePostExcludedFromPublic:
    def test_workspace_post_not_in_public_only_heatmap(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        _make_post(user, workspace_id=ws.id)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)

        assert result["total"] == 0


# ── SC-002 ─────────────────────────────────────────────────────────────────────


class TestWorkspacePostIncludedForOwner:
    def test_workspace_post_counted_when_public_only_false(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        _make_post(user, workspace_id=ws.id)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=False)

        assert result["total"] == 1


# ── SC-003 ─────────────────────────────────────────────────────────────────────


class TestOtherUserExcluded:
    def test_other_users_post_not_counted(self, db_session):
        user = _make_user()
        other = _make_user()
        _make_post(other)  # public post by a different user
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)

        assert result["total"] == 0


# ── SC-004 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceBenchmarkExcluded:
    def test_workspace_benchmark_not_in_public_only_summary(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        post = _make_post(user)
        suite = _make_suite(user, workspace_id=ws.id)
        _make_bench_run(post, user, suite, workspace_id=ws.id)
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=True)

        assert result["benchmarks_run"] == 0

    def test_workspace_benchmark_counted_for_owner(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        post = _make_post(user)
        suite = _make_suite(user, workspace_id=ws.id)
        _make_bench_run(post, user, suite, workspace_id=ws.id)
        _db.session.commit()

        result = build_user_contribution_summary(user.id, public_only=False)

        assert result["benchmarks_run"] == 1


# ── SC-005 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceOntologyExcluded:
    def test_workspace_ontology_mapping_not_in_public_only_view(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        post = _make_post(user)
        node = _make_node(user)
        # Workspace-scoped mapping — must NOT appear in public view
        _map_ontology(post, node, workspace_id=ws.id)
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=True)

        assert result == []

    def test_workspace_ontology_included_for_owner(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        post = _make_post(user, workspace_id=ws.id)
        node = _make_node(user)
        _map_ontology(post, node, workspace_id=ws.id)
        _db.session.commit()

        result = build_ontology_contributions(user.id, public_only=False)

        assert len(result) == 1
        assert result[0]["node"].id == node.id


# ── SC-006 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceRevisionExcluded:
    def test_revision_on_workspace_post_excluded_from_public_heatmap(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        ws_post = _make_post(user, workspace_id=ws.id)
        # Accepted revision on a workspace post — must NOT count in public view
        _make_revision(ws_post, user, accepted=True)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=True)

        assert result["total"] == 0

    def test_revision_on_workspace_post_counted_for_owner(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        ws_post = _make_post(user, workspace_id=ws.id)
        _make_revision(ws_post, user, accepted=True)
        _db.session.commit()

        result = build_contribution_heatmap(user.id, public_only=False)

        # The workspace post (published) + the accepted revision = 2 contributions
        assert result["total"] == 2


# ── SC-007 ─────────────────────────────────────────────────────────────────────


class TestBoundedQueryCount:
    def test_all_four_functions_within_8_queries(self, db_session):
        """Calling all 4 analytics functions issues ≤ 8 DB round-trips total."""
        from sqlalchemy import event  # noqa: PLC0415

        user = _make_user()
        _db.session.commit()

        query_count = [0]

        def _count_query(conn, cursor, stmt, params, ctx, executemany):  # noqa: PLR0913
            query_count[0] += 1

        engine = _db.engine
        event.listen(engine, "before_cursor_execute", _count_query)
        try:
            build_contribution_heatmap(user.id, public_only=True)
            build_user_contribution_summary(user.id, public_only=True)
            build_ontology_contributions(user.id, public_only=True)
            compute_contribution_streak(user.id, public_only=True)
        finally:
            event.remove(engine, "before_cursor_execute", _count_query)

        assert query_count[0] <= 8, (
            f"Expected ≤ 8 DB queries for full analytics render, got {query_count[0]}"
        )
