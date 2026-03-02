"""Tests for Benchmark Suite CRUD operations.

Coverage
--------
  BC-001  create_suite — public (workspace_id IS NULL).
  BC-002  create_suite — workspace-scoped (editor role required).
  BC-003  create_suite — rejects non-member.
  BC-004  create_suite — rejects contributor (below editor).
  BC-005  add_case — adds case to suite; name auto-generated when blank.
  BC-006  add_case — rejects non-dict input_json.
  BC-007  list_suites — public scope returns only public suites.
  BC-008  list_suites — workspace scope returns only workspace suites.
  BC-009  list_suites — unauthenticated returns empty list.
  BC-010  get_suite — returns suite by slug in correct scope.
  BC-011  get_suite — returns None for wrong scope.
  BC-012  cancel_run — queued run is canceled.
  BC-013  cancel_run — completed run raises BenchmarkError.
  BC-014  Public list route returns 200 for authenticated user.
  BC-015  Public detail route returns 200 for authenticated user.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import benchmark_service as bsvc
from backend.services.benchmark_service import BenchmarkError

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"bc{n}@example.com",
        username=f"bcuser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"BC-WS {n}", slug=f"bc-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    member = WorkspaceMember(
        workspace_id=ws.id,
        user_id=owner.id,
        role=WorkspaceMemberRole.owner,
    )
    _db.session.add(member)
    _db.session.flush()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    m = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role)
    _db.session.add(m)
    _db.session.flush()


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"BC-Prompt {n}",
        slug=f"bc-prompt-{n}",
        kind="prompt",
        markdown_body="hello {{name}}",
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


# ── BC-001 ─────────────────────────────────────────────────────────────────────


class TestCreatePublicSuite:
    def test_creates_suite_with_null_workspace(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, "My Public Suite")
        _db.session.commit()
        assert suite.id is not None
        assert suite.workspace_id is None
        assert suite.slug.startswith("my-public-suite")
        assert suite.name == "My Public Suite"
        assert suite.created_by_user_id == user.id


# ── BC-002 ─────────────────────────────────────────────────────────────────────


class TestCreateWorkspaceSuite:
    def test_editor_can_create(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite = bsvc.create_suite(owner, "WS Suite", workspace=ws)
        _db.session.commit()
        assert suite.workspace_id == ws.id

    def test_duplicate_name_uses_different_slug(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        s1 = bsvc.create_suite(owner, "Dupe Suite", workspace=ws)
        s2 = bsvc.create_suite(owner, "Dupe Suite", workspace=ws)
        _db.session.commit()
        assert s1.slug != s2.slug


# ── BC-003 / BC-004 ────────────────────────────────────────────────────────────


class TestCreateSuitePermissions:
    def test_non_member_rejected(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        outsider = _make_user()
        with pytest.raises(BenchmarkError, match="Not a workspace member"):
            bsvc.create_suite(outsider, "Bad Suite", workspace=ws)

    def test_contributor_role_rejected(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        contrib = _make_user()
        _add_member(ws, contrib, WorkspaceMemberRole.contributor)
        with pytest.raises(BenchmarkError, match="Editor role required"):
            bsvc.create_suite(contrib, "No Suite", workspace=ws)

    def test_unauthenticated_rejected(self, db_session):
        with pytest.raises(BenchmarkError):
            bsvc.create_suite(None, "No Suite")


# ── BC-005 / BC-006 ────────────────────────────────────────────────────────────


class TestAddCase:
    def test_adds_case_to_suite(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, "Test Suite")
        case = bsvc.add_case(user, suite, {"var": "hello"}, name="Case A")
        _db.session.commit()
        assert case.id is not None
        assert case.suite_id == suite.id
        assert case.input_json == {"var": "hello"}
        assert case.name == "Case A"

    def test_name_auto_generated_when_blank(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, "Auto Name Suite")
        case = bsvc.add_case(user, suite, {})
        _db.session.commit()
        assert case.name  # not empty

    def test_expected_output_stored(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, "EO Suite")
        case = bsvc.add_case(user, suite, {}, expected_output="The answer is 42")
        _db.session.commit()
        assert case.expected_output == "The answer is 42"

    def test_non_dict_input_json_rejected(self, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, "Bad Suite")
        with pytest.raises(BenchmarkError, match="input_json must be a JSON object"):
            bsvc.add_case(user, suite, ["a", "b"])  # type: ignore[arg-type]


# ── BC-007 / BC-008 / BC-009 ──────────────────────────────────────────────────


class TestListSuites:
    def test_public_scope_returns_public_only(self, db_session):
        user = _make_user()
        owner = _make_user()
        ws = _make_workspace(owner)
        pub_suite = bsvc.create_suite(user, "Public One")
        _ = bsvc.create_suite(owner, "WS One", workspace=ws)
        _db.session.commit()

        suites = bsvc.list_suites(user, workspace=None)
        ids = [s.id for s in suites]
        assert pub_suite.id in ids
        for s in suites:
            assert s.workspace_id is None

    def test_workspace_scope_returns_workspace_only(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        ws_suite = bsvc.create_suite(owner, "In WS", workspace=ws)
        _ = bsvc.create_suite(owner, "Public Two")
        _db.session.commit()

        suites = bsvc.list_suites(owner, workspace=ws)
        assert all(s.workspace_id == ws.id for s in suites)
        assert ws_suite.id in [s.id for s in suites]

    def test_unauthenticated_returns_empty(self, db_session):
        user = _make_user()
        _ = bsvc.create_suite(user, "Public X")
        _db.session.commit()
        assert bsvc.list_suites(None) == []


# ── BC-010 / BC-011 ───────────────────────────────────────────────────────────


class TestGetSuite:
    def test_get_public_suite_by_slug(self, db_session):
        user = _make_user()
        s = bsvc.create_suite(user, "Findable Suite")
        _db.session.commit()
        found = bsvc.get_suite(user, s.slug, workspace=None)
        assert found is not None
        assert found.id == s.id

    def test_workspace_suite_not_found_in_public_scope(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        s = bsvc.create_suite(owner, "WS Only", workspace=ws)
        _db.session.commit()
        result = bsvc.get_suite(owner, s.slug, workspace=None)
        assert result is None


# ── BC-012 / BC-013 ───────────────────────────────────────────────────────────


class TestCancelRun:
    def test_cancel_queued_run(self, db_session):
        from unittest.mock import patch  # noqa: PLC0415

        user = _make_user()
        suite = bsvc.create_suite(user, "Cancel Suite")
        _make_case_for_run = bsvc.add_case(user, suite, {}, name="C1")  # noqa: F841
        prompt = _make_prompt(user)
        _db.session.commit()

        with patch("backend.tasks.benchmark_runs.run_benchmark.delay"):
            run = bsvc.create_run(user, suite, prompt, 1)
            _db.session.commit()

        canceled = bsvc.cancel_run(user, run)
        _db.session.commit()
        assert canceled.status == "canceled"

    def test_cancel_completed_run_raises(self, db_session):
        from unittest.mock import patch  # noqa: PLC0415

        user = _make_user()
        suite = bsvc.create_suite(user, "Compl Suite")
        bsvc.add_case(user, suite, {})
        prompt = _make_prompt(user)
        _db.session.commit()

        with patch("backend.tasks.benchmark_runs.run_benchmark.delay"):
            run = bsvc.create_run(user, suite, prompt, 1)
            run.status = "completed"
            _db.session.commit()

        with pytest.raises(BenchmarkError, match="Cannot cancel"):
            bsvc.cancel_run(user, run)


# ── BC-014 / BC-015 ───────────────────────────────────────────────────────────


class TestPublicRoutes:
    def test_list_route_200(self, auth_client, db_session):
        user = _make_user()
        _login(auth_client, user)
        resp = auth_client.get("/benchmarks/")
        assert resp.status_code == 200

    def test_detail_route_200(self, auth_client, db_session):
        user = _make_user()
        suite = bsvc.create_suite(user, "Route Suite")
        _db.session.commit()
        _login(auth_client, user)
        resp = auth_client.get(f"/benchmarks/{suite.slug}")
        assert resp.status_code == 200

    def test_detail_route_404_for_unknown_slug(self, auth_client, db_session):
        user = _make_user()
        _login(auth_client, user)
        resp = auth_client.get("/benchmarks/no-such-suite-xyz")
        assert resp.status_code == 404

    def test_unauthenticated_redirected(self, auth_client, db_session):
        resp = auth_client.get("/benchmarks/")
        # require_auth returns 302 redirect to login
        assert resp.status_code == 302
