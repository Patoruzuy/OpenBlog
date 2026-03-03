"""Tests for Ontology-Aware Benchmark Slices.

Coverage
--------
  OBS-001  Mapped prompt's completed run appears in list_runs_for_ontology_node.
  OBS-002  Unmapped prompt's run is excluded.
  OBS-003  Descendant-mapped prompt's run is included.
  OBS-004  Workspace-only mapping excluded from public slice.
  OBS-005  Workspace overlay mapping included for workspace member.
  OBS-006  Other-workspace runs excluded from workspace slice.
  OBS-007  GET /ontology/<slug>/benchmarks returns 200.
  OBS-008  GET /w/<ws>/ontology/<slug>/benchmarks returns 200 for member, 404
           for non-member.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

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

_ctr = itertools.count(25_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"obs{n}@example.com", f"obsuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"OBS WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    _db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    _db.session.commit()


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"OBS-Prompt {n}",
        slug=f"obs-prompt-{n}",
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
        f"obs-node-{n}",
        f"OBS Node {n}",
        is_public=True,
        parent_id=parent_id,
    )
    _db.session.commit()
    return node


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"OBS Suite {n}",
        slug=f"obs-suite-{n}",
        created_by_user_id=user.id,
        workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_completed_run(suite, prompt, user, *, score: float = 0.8) -> BenchmarkRun:
    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt.id,
        prompt_version=prompt.version,
        workspace_id=suite.workspace_id,
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(run)
    _db.session.flush()
    # Case + result so it qualifies as a completed run with a score.
    n = _n()
    case = BenchmarkCase(
        suite_id=suite.id,
        name=f"OBS Case {n}",
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


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


def _map(user, prompt, node, *, workspace=None):
    set_mappings(user, prompt, [node.id], workspace=workspace)
    _db.session.commit()


# ── OBS-001 ───────────────────────────────────────────────────────────────────


class TestMappedRunAppears:
    def test_mapped_prompt_run_in_results(self, db_session):
        """OBS-001"""
        admin = _make_user("admin")
        user = _make_user()
        node = _make_node(admin)
        prompt = _make_prompt(user)
        _map(admin, prompt, node)
        suite = _make_suite(user)
        run = _make_completed_run(suite, prompt, user)
        _db.session.commit()

        runs = bsvc.list_runs_for_ontology_node(user, node, workspace=None)
        assert any(r.id == run.id for r in runs), "Expected run to appear in ontology slice"


# ── OBS-002 ───────────────────────────────────────────────────────────────────


class TestUnmappedRunExcluded:
    def test_unmapped_prompt_run_excluded(self, db_session):
        """OBS-002"""
        admin = _make_user("admin")
        user = _make_user()
        node = _make_node(admin)
        prompt = _make_prompt(user)
        # No mapping for prompt → node.
        suite = _make_suite(user)
        run = _make_completed_run(suite, prompt, user)
        _db.session.commit()

        runs = bsvc.list_runs_for_ontology_node(user, node, workspace=None)
        assert not any(r.id == run.id for r in runs), "Unmapped run should be excluded"


# ── OBS-003 ───────────────────────────────────────────────────────────────────


class TestDescendantMappedRunIncluded:
    def test_descendant_mapped_run_included(self, db_session):
        """OBS-003: prompt mapped to a child node → run appears in parent node slice."""
        admin = _make_user("admin")
        user = _make_user()
        parent = _make_node(admin)
        child = _make_node(admin, parent_id=parent.id)
        prompt = _make_prompt(user)
        # Map to child, not parent.
        _map(admin, prompt, child)
        suite = _make_suite(user)
        run = _make_completed_run(suite, prompt, user)
        _db.session.commit()

        runs = bsvc.list_runs_for_ontology_node(user, parent, workspace=None)
        assert any(r.id == run.id for r in runs), (
            "Descendant-mapped prompt run should appear in parent node slice"
        )


# ── OBS-004 ───────────────────────────────────────────────────────────────────


class TestWorkspaceOnlyMappingExcludedFromPublic:
    def test_ws_only_mapping_excluded_from_public_slice(self, db_session):
        """OBS-004"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        prompt = _make_prompt(owner)  # public prompt
        # Workspace-only mapping (workspace_id = ws.id in content_ontology).
        _map(owner, prompt, node, workspace=ws)
        suite = _make_suite(owner)
        run = _make_completed_run(suite, prompt, owner)
        _db.session.commit()

        # Public slice must not see this run (no public mapping exists).
        user = _make_user()
        runs = bsvc.list_runs_for_ontology_node(user, node, workspace=None)
        assert not any(r.id == run.id for r in runs), (
            "Workspace-only mapping must not bleed into public slice"
        )


# ── OBS-005 ───────────────────────────────────────────────────────────────────


class TestWorkspaceOverlayIncluded:
    def test_ws_overlay_mapping_included_for_member(self, db_session):
        """OBS-005"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        prompt = _make_prompt(owner)
        # Workspace overlay mapping.
        _map(owner, prompt, node, workspace=ws)
        suite = _make_suite(owner, workspace_id=ws.id)
        run = _make_completed_run(suite, prompt, owner)
        _db.session.commit()

        runs = bsvc.list_runs_for_ontology_node(owner, node, workspace=ws)
        assert any(r.id == run.id for r in runs), "Workspace overlay run should appear for members"


# ── OBS-006 ───────────────────────────────────────────────────────────────────


class TestOtherWorkspaceRunsExcluded:
    def test_other_workspace_runs_excluded(self, db_session):
        """OBS-006"""
        admin = _make_user("admin")
        owner_a = _make_user("editor")
        owner_b = _make_user("editor")
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        node = _make_node(admin)

        prompt = _make_prompt(owner_b)
        _map(owner_b, prompt, node, workspace=ws_b)
        suite_b = _make_suite(owner_b, workspace_id=ws_b.id)
        run_b = _make_completed_run(suite_b, prompt, owner_b)
        _db.session.commit()

        # Owner of ws_a should not see ws_b's run.
        runs = bsvc.list_runs_for_ontology_node(owner_a, node, workspace=ws_a)
        assert not any(r.id == run_b.id for r in runs), (
            "Cross-workspace runs must never be visible"
        )


# ── OBS-007 ───────────────────────────────────────────────────────────────────


class TestPublicNodeBenchmarksRoute:
    def test_returns_200(self, db_session, auth_client):
        """OBS-007"""
        admin = _make_user("admin")
        user = _make_user()
        node = _make_node(admin)
        _login(auth_client, user)

        resp = auth_client.get(f"/ontology/{node.slug}/benchmarks")
        assert resp.status_code == 200

    def test_missing_node_returns_404(self, db_session, auth_client):
        """OBS-007 – non-existent slug"""
        resp = auth_client.get("/ontology/does-not-exist-xyz/benchmarks")
        assert resp.status_code == 404


# ── OBS-008 ───────────────────────────────────────────────────────────────────


class TestWsNodeBenchmarksRoute:
    def test_member_gets_200(self, db_session, auth_client):
        """OBS-008 – member"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        _login(auth_client, owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/benchmarks")
        assert resp.status_code == 200

    def test_non_member_gets_404(self, db_session, auth_client):
        """OBS-008 – non-member"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        outsider = _make_user()
        ws = _make_workspace(owner)
        node = _make_node(admin)
        _login(auth_client, outsider)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/benchmarks")
        assert resp.status_code == 404
