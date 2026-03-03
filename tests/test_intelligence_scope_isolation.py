"""Tests for Intelligence Dashboard scope isolation.

Coverage
--------
  ISO-001  Workspace-A prompt does NOT appear in public top_prompts.
  ISO-002  Workspace-A prompt does NOT appear in workspace-B view.
  ISO-003  Public prompt IS visible in workspace-A view.
  ISO-004  Workspace-B run is NOT visible in workspace-A top_prompts.
  ISO-005  Public route shows no workspace-scoped ontology mappings.
  ISO-006  Non-prompt Post (kind != 'prompt') is excluded from all views.
  ISO-007  Draft prompt (status != 'published') is excluded from all views.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import intelligence_service as intel_svc

_ctr = itertools.count(4000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"iso{n}@example.com",
        username=f"isouser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"ISO-WS {n}", slug=f"iso-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _make_prompt(author, *, workspace_id=None):
    n = _n()
    p = Post(
        title=f"ISO-Prompt {n}",
        slug=f"iso-prompt-{n}",
        kind="prompt",
        markdown_body="hello",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_run(post, score: float, workspace_id=None):
    n = _n()
    dt = datetime.now(UTC) - timedelta(days=5)
    suite = BenchmarkSuite(
        name=f"ISO-Suite {n}",
        slug=f"iso-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(suite)
    _db.session.flush()

    case_ = BenchmarkCase(
        suite_id=suite.id,
        name=f"ISO-Case {n}",
        input_json={},
        created_at=datetime.now(UTC),
    )
    _db.session.add(case_)
    _db.session.flush()

    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=post.id,
        prompt_version=1,
        workspace_id=workspace_id,
        model_name="test-model",
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=post.author_id,
        created_at=dt,
        completed_at=dt,
    )
    _db.session.add(run)
    _db.session.flush()

    result = BenchmarkRunResult(
        run_id=run.id,
        case_id=case_.id,
        output_text="ok",
        score_numeric=score,
        created_at=dt,
    )
    _db.session.add(result)
    _db.session.flush()
    return run, result


# ── ISO-001 ─────────────────────────────────────────────────────────────────────


class TestWorkspacePromptNotInPublic:
    def test_ws_a_prompt_absent_from_public_top_prompts(self, db_session):
        user_a = _make_user()
        ws_a = _make_workspace(user_a)
        ws_prompt = _make_prompt(user_a, workspace_id=ws_a.id)
        _make_run(ws_prompt, 0.95, workspace_id=ws_a.id)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=None)
        slugs = [r.slug for r in rows]
        assert ws_prompt.slug not in slugs


# ── ISO-002 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceNotVisibleInOtherWorkspace:
    def test_ws_a_prompt_absent_from_ws_b_view(self, db_session):
        user_a = _make_user()
        user_b = _make_user()
        ws_a = _make_workspace(user_a)
        ws_b = _make_workspace(user_b)

        ws_a_prompt = _make_prompt(user_a, workspace_id=ws_a.id)
        _make_run(ws_a_prompt, 0.90, workspace_id=ws_a.id)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=ws_b)
        slugs = [r.slug for r in rows]
        assert ws_a_prompt.slug not in slugs


# ── ISO-003 ─────────────────────────────────────────────────────────────────────


class TestPublicPromptVisibleInWorkspace:
    def test_public_prompt_visible_in_ws_a_view(self, db_session):
        user_a = _make_user()
        ws_a = _make_workspace(user_a)
        pub_prompt = _make_prompt(user_a, workspace_id=None)
        _make_run(pub_prompt, 0.75, workspace_id=None)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=ws_a)
        slugs = [r.slug for r in rows]
        assert pub_prompt.slug in slugs


# ── ISO-004 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceBRunNotInWorkspaceA:
    def test_ws_b_run_absent_from_ws_a_top_prompts(self, db_session):
        user_a = _make_user()
        user_b = _make_user()
        ws_a = _make_workspace(user_a)
        ws_b = _make_workspace(user_b)

        # prompt in ws_b; run in ws_b
        ws_b_prompt = _make_prompt(user_b, workspace_id=ws_b.id)
        _make_run(ws_b_prompt, 0.88, workspace_id=ws_b.id)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=ws_a)
        slugs = [r.slug for r in rows]
        assert ws_b_prompt.slug not in slugs


# ── ISO-005 ─────────────────────────────────────────────────────────────────────


class TestPublicOntologyMappingIsolation:
    def test_ws_ontology_mapping_absent_from_public_view(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)

        pub_prompt = _make_prompt(user, workspace_id=None)
        _make_run(pub_prompt, 0.70, workspace_id=None)

        node = OntologyNode(
            slug=f"iso-node-{_n()}",
            name="ISO WS Category",
            created_by_user_id=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(node)
        _db.session.flush()

        # Workspace-scoped mapping — must NOT appear in public ontology performance
        ws_mapping = ContentOntology(
            post_id=pub_prompt.id,
            ontology_node_id=node.id,
            workspace_id=ws.id,  # workspace-scoped
            created_by_user_id=user.id,
            created_at=datetime.now(UTC),
        )
        _db.session.add(ws_mapping)
        _db.session.flush()
        _db.session.commit()

        rows = intel_svc.get_ontology_performance(workspace=None)
        # The workspace-scoped mapping is not a public mapping, so node should
        # NOT appear in the public view
        node_names = [r.node_name for r in rows]
        assert "ISO WS Category" not in node_names


# ── ISO-006 ─────────────────────────────────────────────────────────────────────


class TestNonPromptPostExcluded:
    """Posts with kind != 'prompt' must never appear in the dashboard."""

    def test_article_post_absent_from_top_prompts(self, db_session):
        user = _make_user()
        n = _n()
        article = Post(
            title=f"ISO-Article {n}",
            slug=f"iso-article-{n}",
            kind="post",  # not a prompt
            markdown_body="hello",
            status=PostStatus.published,
            author_id=user.id,
            workspace_id=None,
        )
        _db.session.add(article)
        _db.session.flush()
        _make_run(article, 0.99, workspace_id=None)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=None)
        slugs = [r.slug for r in rows]
        assert article.slug not in slugs


# ── ISO-007 ─────────────────────────────────────────────────────────────────────


class TestDraftPromptExcluded:
    """Draft prompts must never appear in the dashboard."""

    def test_draft_prompt_absent_from_top_prompts(self, db_session):
        user = _make_user()
        n = _n()
        draft = Post(
            title=f"ISO-Draft {n}",
            slug=f"iso-draft-{n}",
            kind="prompt",
            markdown_body="hello",
            status=PostStatus.draft,  # not published
            author_id=user.id,
            workspace_id=None,
        )
        _db.session.add(draft)
        _db.session.flush()
        _make_run(draft, 0.95, workspace_id=None)
        _db.session.commit()

        rows = intel_svc.get_top_prompts(workspace=None)
        slugs = [r.slug for r in rows]
        assert draft.slug not in slugs
