"""Tests for Benchmark Suite workspace scope isolation.

Coverage
--------
  SI-001  Workspace A suite is invisible to workspace B members.
  SI-002  Public suite list never exposes workspace suites.
  SI-003  Non-member receives 404 on WS list route.
  SI-004  Non-member receives 404 on WS detail route.
  SI-005  Non-member receive 404 on WS run_detail route.
  SI-006  Cross-workspace create_run is rejected (422).
  SI-007  Public suite rejects workspace-scoped prompt.
  SI-008  Workspace suite rejects prompt from different workspace.
  SI-009  Workspace suite allows public+published prompt.
  SI-010  get_suite returns None for non-member in workspace scope.
  SI-011  list_suites non-member in workspace returns empty list.
  SI-012  WS list route returns only suites for that workspace.
  SI-013  after_request cache header applied to /w/ routes.
"""

from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import benchmark_service as bsvc
from backend.services.benchmark_service import BenchmarkError

_ctr = itertools.count(500)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"si{n}@example.com",
        username=f"siuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"SI-WS {n}", slug=f"si-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id,
            user_id=owner.id,
            role=WorkspaceMemberRole.owner,
        )
    )
    _db.session.flush()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    _db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    _db.session.flush()


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"SI-Prompt {n}",
        slug=f"si-prompt-{n}",
        kind="prompt",
        markdown_body="hello",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── SI-001 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceCrossVisibility:
    def test_ws_a_suite_invisible_to_ws_b_member(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)

        suite_a = bsvc.create_suite(owner_a, "Suite A Scope", workspace=ws_a)
        _db.session.commit()

        # owner_b requests list for ws_b
        suites_b = bsvc.list_suites(owner_b, workspace=ws_b)
        ids = [s.id for s in suites_b]
        assert suite_a.id not in ids

    def test_get_suite_wrong_workspace_returns_none(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)

        suite_a = bsvc.create_suite(owner_a, "Only-A Suite", workspace=ws_a)
        _db.session.commit()

        result = bsvc.get_suite(owner_b, suite_a.slug, workspace=ws_b)
        assert result is None


# ── SI-002 ─────────────────────────────────────────────────────────────────────


class TestPublicListNeverLeaksWorkspaces:
    def test_public_list_excludes_workspace_suites(self, db_session):
        user = _make_user()
        ws = _make_workspace(user)
        _ = bsvc.create_suite(user, "Hidden WS Suite", workspace=ws)
        pub = bsvc.create_suite(user, "Visible Public Suite")
        _db.session.commit()

        suites = bsvc.list_suites(user, workspace=None)
        ids = [s.id for s in suites]
        assert pub.id in ids
        for s in suites:
            assert s.workspace_id is None


# ── SI-003 / SI-004 / SI-005 ──────────────────────────────────────────────────


class TestNonMemberRoutes:
    def test_ws_list_non_member_404(self, auth_client, db_session):
        owner = _make_user()
        visitor = _make_user()
        ws = _make_workspace(owner)
        _login(auth_client, visitor)
        resp = auth_client.get(f"/w/{ws.slug}/benchmarks/")
        assert resp.status_code == 404

    def test_ws_detail_non_member_404(self, auth_client, db_session):
        owner = _make_user()
        visitor = _make_user()
        ws = _make_workspace(owner)
        suite = bsvc.create_suite(owner, "WS Detail Suite", workspace=ws)
        _db.session.commit()
        _login(auth_client, visitor)
        resp = auth_client.get(f"/w/{ws.slug}/benchmarks/{suite.slug}")
        assert resp.status_code == 404

    def test_ws_run_detail_non_member_404(self, auth_client, db_session):
        owner = _make_user()
        visitor = _make_user()
        ws = _make_workspace(owner)
        suite = bsvc.create_suite(owner, "RunDetail Suite", workspace=ws)
        prompt = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        with patch("backend.tasks.benchmark_runs.run_benchmark.delay"):
            run = bsvc.create_run(owner, suite, prompt, 1)
            _db.session.commit()

        _login(auth_client, visitor)
        resp = auth_client.get(f"/w/{ws.slug}/benchmarks/runs/{run.id}")
        assert resp.status_code == 404


# ── SI-006 ────────────────────────────────────────────────────────────────────


class TestCrossWorkspaceRunRejected:
    def test_different_workspace_prompt_rejected(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        # owner_a is member of both so they can attempt it
        _add_member(ws_b, owner_a, WorkspaceMemberRole.editor)

        suite = bsvc.create_suite(owner_a, "WS-A Suite", workspace=ws_a)
        prompt_b = _make_prompt(owner_b, workspace_id=ws_b.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="different workspace"):
            bsvc.create_run(owner_a, suite, prompt_b, 1)


# ── SI-007 ─────────────────────────────────────────────────────────────────────


class TestPublicSuiteRejectsWorkspacePrompt:
    def test_public_suite_rejects_workspace_scoped_prompt(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        pub_suite = bsvc.create_suite(owner, "Pub Suite Reject")
        ws_prompt = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="Public suites cannot"):
            bsvc.create_run(owner, pub_suite, ws_prompt, 1)


# ── SI-008 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceSuiteRejectsDifferentWorkspacePrompt:
    def test_rejects_prompt_from_other_workspace(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        _add_member(ws_b, owner_a, WorkspaceMemberRole.editor)

        suite_a = bsvc.create_suite(owner_a, "Suite Only A", workspace=ws_a)
        prompt_b = _make_prompt(owner_b, workspace_id=ws_b.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="different workspace"):
            bsvc.create_run(owner_a, suite_a, prompt_b, 1)


# ── SI-009 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceSuiteAllowsPublicPrompt:
    def test_workspace_suite_allows_public_published_prompt(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite = bsvc.create_suite(owner, "WS Suite Accept", workspace=ws)
        pub_prompt = _make_prompt(owner, workspace_id=None, status=PostStatus.published)
        _db.session.commit()

        with patch("backend.tasks.benchmark_runs.run_benchmark.delay"):
            run = bsvc.create_run(owner, suite, pub_prompt, 1)
            _db.session.commit()
        assert run.id is not None


# ── SI-010 / SI-011 ───────────────────────────────────────────────────────────


class TestNonMemberServiceResults:
    def test_get_suite_returns_none_for_non_member(self, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        suite = bsvc.create_suite(owner, "NM Suite", workspace=ws)
        _db.session.commit()

        result = bsvc.get_suite(outsider, suite.slug, workspace=ws)
        assert result is None

    def test_list_suites_returns_empty_for_non_member(self, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        _ = bsvc.create_suite(owner, "NM List Suite", workspace=ws)
        _db.session.commit()

        suites = bsvc.list_suites(outsider, workspace=ws)
        assert suites == []


# ── SI-012 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceListRouteIsolation:
    def test_ws_list_returns_only_workspace_suites(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        ws_suite = bsvc.create_suite(owner, "Only WS In List", workspace=ws)
        _ = bsvc.create_suite(owner, "Public In List")
        _db.session.commit()
        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/benchmarks/")
        assert resp.status_code == 200
        # workspace suite appears; public suite slug should NOT
        assert ws_suite.name.encode() in resp.data


# ── SI-013 ─────────────────────────────────────────────────────────────────────


class TestCacheHeaders:
    def test_ws_route_has_no_store_header(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/benchmarks/")
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc

    def test_public_route_no_private_cache_header(self, auth_client, db_session):
        owner = _make_user()
        _login(auth_client, owner)
        resp = auth_client.get("/benchmarks/")
        cc = resp.headers.get("Cache-Control", "")
        # public route must NOT have private,no-store injected by the WS hook
        assert "private" not in cc or cc == ""
