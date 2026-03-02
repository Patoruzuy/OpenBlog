"""Tests for A/B Experiment CRUD operations.

Coverage
--------
  ABCR-001  create public experiment (workspace_id NULL).
  ABCR-002  create workspace experiment (editor+ required).
  ABCR-003  non-member rejected.
  ABCR-004  contributor rejected (below editor).
  ABCR-005  public experiment rejects workspace-scoped suite.
  ABCR-006  public experiment rejects workspace-scoped variant prompt.
  ABCR-007  workspace experiment rejects different-workspace suite.
  ABCR-008  same prompt + same version rejected.
  ABCR-009  different versions of same prompt allowed.
  ABCR-010  different prompts + same version allowed.
  ABCR-011  list_experiments public scope excludes workspace experiments.
  ABCR-012  list_experiments workspace scope excludes public experiments.
  ABCR-013  list_experiments unauthenticated returns empty.
  ABCR-014  get_experiment returns experiment by slug in public scope.
  ABCR-015  get_experiment returns None for wrong scope.
  ABCR-016  Public list route 200.
  ABCR-017  Public detail route 200 for draft experiment.
  ABCR-018  Unauthenticated redirected.
  ABCR-019  draft prompt rejected in variant.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.benchmark import BenchmarkSuite
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import ab_experiment_service as ab_svc
from backend.services.benchmark_service import BenchmarkError

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"abcr{n}@example.com",
        username=f"abcruser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"ABCR-WS {n}", slug=f"abcr-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner)
    )
    _db.session.flush()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    _db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    _db.session.flush()


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"ABCR-Prompt {n}", slug=f"abcr-prompt-{n}", kind="prompt",
        markdown_body="hello {{name}}", status=status,
        author_id=author.id, workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"ABCR Suite {n}", slug=f"abcr-suite-{n}",
        created_by_user_id=user.id, workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── ABCR-001 ──────────────────────────────────────────────────────────────────


class TestCreatePublicExperiment:
    def test_creates_with_null_workspace(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()

        exp = ab_svc.create_experiment(user, "My Exp", suite, pa, 1, pb, 2)
        _db.session.commit()

        assert exp.id is not None
        assert exp.workspace_id is None
        assert exp.status == "draft"
        assert exp.variant_a_prompt_post_id == pa.id
        assert exp.variant_a_version == 1
        assert exp.variant_b_prompt_post_id == pb.id
        assert exp.variant_b_version == 2


# ── ABCR-002 ──────────────────────────────────────────────────────────────────


class TestCreateWorkspaceExperiment:
    def test_owner_can_create(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        exp = ab_svc.create_experiment(
            owner, "WS Exp", suite, pa, 1, pb, 2, workspace=ws
        )
        _db.session.commit()
        assert exp.workspace_id == ws.id


# ── ABCR-003 / ABCR-004 ───────────────────────────────────────────────────────


class TestCreatePermissions:
    def test_non_member_rejected(self, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="Not a workspace member"):
            ab_svc.create_experiment(outsider, "Bad", suite, pa, 1, pb, 2, workspace=ws)

    def test_contributor_rejected(self, db_session):
        owner = _make_user()
        contrib = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, contrib, WorkspaceMemberRole.contributor)
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="Editor role required"):
            ab_svc.create_experiment(contrib, "Bad", suite, pa, 1, pb, 2, workspace=ws)


# ── ABCR-005 ──────────────────────────────────────────────────────────────────


class TestPublicExperimentSuiteScope:
    def test_rejects_workspace_scoped_suite(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        ws_suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner)
        pb = _make_prompt(owner)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="public suite"):
            ab_svc.create_experiment(owner, "Bad", ws_suite, pa, 1, pb, 2)


# ── ABCR-006 ──────────────────────────────────────────────────────────────────


class TestPublicExperimentVariantScope:
    def test_rejects_workspace_variant_a(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite = _make_suite(owner)
        ws_prompt = _make_prompt(owner, workspace_id=ws.id)
        pub_prompt = _make_prompt(owner)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="public prompts"):
            ab_svc.create_experiment(owner, "Bad", suite, ws_prompt, 1, pub_prompt, 1)

    def test_rejects_workspace_variant_b(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite = _make_suite(owner)
        pub_prompt = _make_prompt(owner)
        ws_prompt = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="public prompts"):
            ab_svc.create_experiment(owner, "Bad", suite, pub_prompt, 1, ws_prompt, 1)

    def test_rejects_draft_variant(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        draft = _make_prompt(user, status=PostStatus.draft)
        pub = _make_prompt(user)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="only published"):
            ab_svc.create_experiment(user, "Draft Exp", suite, draft, 1, pub, 1)


# ── ABCR-007 ──────────────────────────────────────────────────────────────────


class TestWorkspaceExperimentSuiteScope:
    def test_rejects_different_workspace_suite(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        _add_member(ws_b, owner_a, WorkspaceMemberRole.editor)
        suite_a = _make_suite(owner_a, workspace_id=ws_a.id)
        pa = _make_prompt(owner_a, workspace_id=ws_a.id)
        pb = _make_prompt(owner_a, workspace_id=ws_a.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="does not belong to this workspace"):
            ab_svc.create_experiment(
                owner_a, "Bad", suite_a, pa, 1, pb, 2, workspace=ws_b
            )


# ── ABCR-008 / ABCR-009 / ABCR-010 ───────────────────────────────────────────


class TestVariantDistinctness:
    def test_same_prompt_same_version_rejected(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        p = _make_prompt(user)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="must differ"):
            ab_svc.create_experiment(user, "Same", suite, p, 1, p, 1)

    def test_same_prompt_different_version_allowed(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        p = _make_prompt(user)
        _db.session.commit()

        exp = ab_svc.create_experiment(user, "DiffVer", suite, p, 1, p, 2)
        _db.session.commit()
        assert exp.id is not None

    def test_different_prompts_same_version_allowed(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()

        exp = ab_svc.create_experiment(user, "DiffPrompts", suite, pa, 1, pb, 1)
        _db.session.commit()
        assert exp.id is not None


# ── ABCR-011 / ABCR-012 / ABCR-013 ───────────────────────────────────────────


class TestListExperiments:
    def test_public_scope_excludes_workspace_experiments(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite_pub = _make_suite(owner)
        suite_ws = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner)
        pb = _make_prompt(owner)
        pa_ws = _make_prompt(owner, workspace_id=ws.id)
        pb_ws = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        pub_exp = ab_svc.create_experiment(owner, "PubExp", suite_pub, pa, 1, pb, 2)
        _db_ws_exp = ab_svc.create_experiment(
            owner, "WSExp", suite_ws, pa_ws, 1, pb_ws, 2, workspace=ws
        )
        _db.session.commit()

        items = ab_svc.list_experiments(owner, workspace=None)
        ids = [e.id for e in items]
        assert pub_exp.id in ids
        for e in items:
            assert e.workspace_id is None

    def test_workspace_scope_excludes_public(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite_pub = _make_suite(owner)
        suite_ws = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner)
        pb = _make_prompt(owner)
        pa_ws = _make_prompt(owner, workspace_id=ws.id)
        pb_ws = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        _pub = ab_svc.create_experiment(owner, "PubExp2", suite_pub, pa, 1, pb, 2)
        ws_exp = ab_svc.create_experiment(
            owner, "WSExp2", suite_ws, pa_ws, 1, pb_ws, 2, workspace=ws
        )
        _db.session.commit()

        items = ab_svc.list_experiments(owner, workspace=ws)
        ids = [e.id for e in items]
        assert ws_exp.id in ids
        for e in items:
            assert e.workspace_id == ws.id

    def test_unauthenticated_returns_empty(self, db_session):
        assert ab_svc.list_experiments(None) == []


# ── ABCR-014 / ABCR-015 ───────────────────────────────────────────────────────


class TestGetExperiment:
    def test_returns_public_experiment_by_slug(self, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()

        exp = ab_svc.create_experiment(user, "FindMe", suite, pa, 1, pb, 2)
        _db.session.commit()

        found = ab_svc.get_experiment(user, exp.slug)
        assert found is not None
        assert found.id == exp.id

    def test_ws_experiment_not_visible_in_public_scope(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        exp = ab_svc.create_experiment(
            owner, "WS Only", suite, pa, 1, pb, 2, workspace=ws
        )
        _db.session.commit()

        result = ab_svc.get_experiment(owner, exp.slug, workspace=None)
        assert result is None


# ── ABCR-016 / ABCR-017 / ABCR-018 ───────────────────────────────────────────


class TestPublicRoutes:
    def test_list_route_200(self, auth_client, db_session):
        user = _make_user()
        _login(auth_client, user)
        resp = auth_client.get("/ab")
        assert resp.status_code == 200

    def test_detail_route_200(self, auth_client, db_session):
        user = _make_user()
        suite = _make_suite(user)
        pa = _make_prompt(user)
        pb = _make_prompt(user)
        _db.session.commit()
        exp = ab_svc.create_experiment(user, "RouteExp", suite, pa, 1, pb, 2)
        _db.session.commit()
        _login(auth_client, user)
        resp = auth_client.get(f"/ab/{exp.slug}")
        assert resp.status_code == 200

    def test_unauthenticated_redirected(self, auth_client, db_session):
        resp = auth_client.get("/ab")
        assert resp.status_code == 302
