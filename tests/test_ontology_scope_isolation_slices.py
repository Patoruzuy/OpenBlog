"""Tests for Ontology Scope Isolation — Benchmark and Recommendation Slices.

Coverage
--------
  OSI-001  Public benchmark slice never shows workspace-only mapping data.
  OSI-002  Workspace benchmark slice shows public + workspace overlay runs.
  OSI-003  Other-workspace runs not visible in workspace benchmark slice.
  OSI-004  GET /w/<ws>/ontology/<slug>/benchmarks carries Cache-Control:
           private, no-store.
  OSI-005  GET /w/<ws>/ontology/<slug>/recommendations carries Cache-Control:
           private, no-store.
  OSI-006  Unauthenticated user gets 404 on both workspace slice routes.
  OSI-007  Benchmark runs are returned in deterministic order:
           created_at DESC, id DESC.
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
from backend.models.post import Post, PostStatus
from backend.models.workspace import WorkspaceMember, WorkspaceMemberRole
from backend.services import benchmark_service as bsvc
from backend.services import workspace_service as ws_svc
from backend.services.content_ontology_service import set_mappings
from backend.services.ontology_service import create_node

_ctr = itertools.count(27_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"osi{n}@example.com", f"osiuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"OSI WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    _db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    _db.session.commit()


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"OSI-Prompt {n}",
        slug=f"osi-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        version=1,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_node(admin, *, parent_id=None):
    n = _n()
    node = create_node(
        admin,
        f"osi-node-{n}",
        f"OSI Node {n}",
        is_public=True,
        parent_id=parent_id,
    )
    _db.session.commit()
    return node


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"OSI Suite {n}",
        slug=f"osi-suite-{n}",
        created_by_user_id=user.id,
        workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_completed_run(
    suite, prompt, user, *, score: float = 0.8, created_at: datetime | None = None
) -> BenchmarkRun:
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=prompt.version,
        workspace_id=suite.workspace_id,
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=user.id,
        created_at=created_at or datetime.now(UTC),
    )
    _db.session.add(run)
    _db.session.flush()
    n = _n()
    case = BenchmarkCase(
        suite_id=suite.id,
        name=f"OSI Case {n}",
        input_json={"q": "test"},
    )
    _db.session.add(case)
    _db.session.flush()
    _db.session.add(
        BenchmarkRunResult(
            run_id=run.id,
            case_id=case.id,
            output_text="output",
            score_numeric=score,
        )
    )
    _db.session.flush()
    return run


def _map(user, prompt, node, *, workspace=None):
    set_mappings(user, prompt, [node.id], workspace=workspace)
    _db.session.commit()


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── OSI-001 ───────────────────────────────────────────────────────────────────


class TestPublicSliceNeverShowsWsData:
    def test_ws_only_mapping_not_in_public_slice(self, db_session):
        """OSI-001: workspace-only mapping must never surface in public benchmark slice."""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        prompt = _make_prompt(owner)
        # Only workspace mapping.
        _map(owner, prompt, node, workspace=ws)
        suite = _make_suite(owner)
        run = _make_completed_run(suite, prompt, owner)
        _db.session.commit()

        reader = _make_user()
        public_runs = bsvc.list_runs_for_ontology_node(reader, node, workspace=None)
        assert not any(r.id == run.id for r in public_runs), (
            "Workspace-only mapping must not appear in public slice"
        )


# ── OSI-002 ───────────────────────────────────────────────────────────────────


class TestWsSliceShowsPublicAndOverlay:
    def test_ws_slice_includes_public_and_overlay_runs(self, db_session):
        """OSI-002"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)

        # Public prompt + public mapping + public suite.
        pub_prompt = _make_prompt(owner)
        _map(admin, pub_prompt, node)  # public mapping
        pub_suite = _make_suite(owner)
        pub_run = _make_completed_run(pub_suite, pub_prompt, owner)

        # Workspace prompt + workspace overlay mapping + workspace suite.
        ws_prompt = _make_prompt(owner, workspace_id=ws.id)
        _map(owner, ws_prompt, node, workspace=ws)
        ws_suite = _make_suite(owner, workspace_id=ws.id)
        ws_run = _make_completed_run(ws_suite, ws_prompt, owner)
        _db.session.commit()

        runs = bsvc.list_runs_for_ontology_node(owner, node, workspace=ws)
        run_ids = {r.id for r in runs}
        assert pub_run.id in run_ids, "Public run should appear in workspace slice"
        assert ws_run.id in run_ids, "Workspace overlay run should appear in workspace slice"


# ── OSI-003 ───────────────────────────────────────────────────────────────────


class TestOtherWorkspaceRunsExcluded:
    def test_other_ws_runs_not_visible(self, db_session):
        """OSI-003"""
        admin = _make_user("admin")
        owner_a = _make_user("editor")
        owner_b = _make_user("editor")
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        node = _make_node(admin)

        # ws_b has a workspace overlay mapping.
        prompt_b = _make_prompt(owner_b)
        _map(owner_b, prompt_b, node, workspace=ws_b)
        suite_b = _make_suite(owner_b, workspace_id=ws_b.id)
        run_b = _make_completed_run(suite_b, prompt_b, owner_b)
        _db.session.commit()

        # ws_a member must not see ws_b's run.
        runs_a = bsvc.list_runs_for_ontology_node(owner_a, node, workspace=ws_a)
        assert not any(r.id == run_b.id for r in runs_a), (
            "Cross-workspace benchmark runs must not be visible"
        )


# ── OSI-004 ───────────────────────────────────────────────────────────────────


class TestBenchmarkRoutesCacheHeaders:
    def test_ws_benchmarks_cache_control(self, db_session, auth_client):
        """OSI-004"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        _login(auth_client, owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/benchmarks")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc, f"Expected 'private' in Cache-Control, got: {cc}"
        assert "no-store" in cc, f"Expected 'no-store' in Cache-Control, got: {cc}"


# ── OSI-005 ───────────────────────────────────────────────────────────────────

class TestRecommendationsRouteCacheHeaders:
    def test_ws_recommendations_cache_control(self, db_session, auth_client):
        """OSI-005"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        _login(auth_client, owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/recommendations")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc, f"Expected 'private' in Cache-Control, got: {cc}"
        assert "no-store" in cc, f"Expected 'no-store' in Cache-Control, got: {cc}"


# ── OSI-006 ───────────────────────────────────────────────────────────────────


class TestUnauthenticatedWorkspaceRoutes:
    def test_unauthenticated_benchmarks_404(self, db_session, auth_client):
        """OSI-006 – benchmarks route"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        # No login → anonymous request.

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/benchmarks")
        assert resp.status_code == 404

    def test_unauthenticated_recommendations_404(self, db_session, auth_client):
        """OSI-006 – recommendations route"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/recommendations")
        assert resp.status_code == 404


# ── OSI-007 ───────────────────────────────────────────────────────────────────


class TestDeterministicOrdering:
    def test_runs_ordered_by_created_at_desc(self, db_session):
        """OSI-007: benchmark runs returned in deterministic order (created_at DESC)."""
        admin = _make_user("admin")
        user = _make_user()
        node = _make_node(admin)

        now = datetime.now(UTC)
        older_ts = now - timedelta(days=2)
        newer_ts = now - timedelta(days=1)

        prompt = _make_prompt(user)
        _map(admin, prompt, node)
        suite = _make_suite(user)

        # Create older run first, then newer.
        older_run = _make_completed_run(suite, prompt, user, created_at=older_ts)
        newer_run = _make_completed_run(suite, prompt, user, created_at=newer_ts)
        _db.session.commit()

        runs = bsvc.list_runs_for_ontology_node(user, node, workspace=None)
        run_ids = [r.id for r in runs]
        assert run_ids.index(newer_run.id) < run_ids.index(older_run.id), (
            f"Newer run (id={newer_run.id}) should appear before older run (id={older_run.id})"
        )
