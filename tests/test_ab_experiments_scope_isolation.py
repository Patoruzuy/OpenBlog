"""Tests for A/B Experiment scope isolation and access control.

Coverage
--------
  ABSI-001  Workspace-A experiment invisible to workspace-B member.
  ABSI-002  Public list never leaks workspace experiments.
  ABSI-003  Non-member GET /w/<ws>/ab → 403/404.
  ABSI-004  Non-member GET /w/<ws>/ab/<slug> → 403/404.
  ABSI-005  Non-member POST /w/<ws>/ab/<slug>/start → 403/404.
  ABSI-006  Cross-workspace prompt rejected in create_experiment.
  ABSI-007  Workspace experiment allows public+published prompt.
  ABSI-008  /w/<ws>/ab route sets Cache-Control: private, no-store.
  ABSI-009  Viewer-level workspace member cannot create experiment.
  ABSI-010  Editor-level workspace member can create experiment.
  ABSI-011  Public list route has no workspace-scoped experiments.
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

_ctr = itertools.count(1_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"absi{n}@example.com",
        username=f"absiuser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"ABSI-WS {n}", slug=f"absi-ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
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
        title=f"ABSI-Prompt {n}",
        slug=f"absi-prompt-{n}",
        kind="prompt",
        markdown_body="hello {{name}}",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_suite(user, *, workspace_id=None):
    n = _n()
    s = BenchmarkSuite(
        name=f"ABSI Suite {n}",
        slug=f"absi-suite-{n}",
        created_by_user_id=user.id,
        workspace_id=workspace_id,
    )
    _db.session.add(s)
    _db.session.flush()
    return s


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── ABSI-001 ──────────────────────────────────────────────────────────────────


class TestCrossWorkspaceInvisibility:
    def test_ws_a_experiment_invisible_to_ws_b_member(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        _make_workspace(owner_b)
        suite_a = _make_suite(owner_a, workspace_id=ws_a.id)
        pa = _make_prompt(owner_a, workspace_id=ws_a.id)
        pb = _make_prompt(owner_a, workspace_id=ws_a.id)
        _db.session.commit()

        exp_a = ab_svc.create_experiment(
            owner_a, "ws-a-exp", suite_a, pa, 1, pb, 2, workspace=ws_a
        )
        _db.session.commit()

        # owner_b has no membership in ws_a
        result = ab_svc.get_experiment(owner_b, exp_a.slug, workspace=ws_a)
        assert result is None

    def test_list_experiments_ws_b_does_not_include_ws_a(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        suite_a = _make_suite(owner_a, workspace_id=ws_a.id)
        pa = _make_prompt(owner_a, workspace_id=ws_a.id)
        pb = _make_prompt(owner_a, workspace_id=ws_a.id)
        _db.session.commit()

        exp_a = ab_svc.create_experiment(
            owner_a, "ws-a-exp-list", suite_a, pa, 1, pb, 2, workspace=ws_a
        )
        _db.session.commit()

        items = ab_svc.list_experiments(owner_b, workspace=ws_b)
        ids = [e.id for e in items]
        assert exp_a.id not in ids


# ── ABSI-002 ──────────────────────────────────────────────────────────────────


class TestPublicListLeakage:
    def test_public_list_does_not_include_ws_experiments(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite_ws = _make_suite(owner, workspace_id=ws.id)
        pa_ws = _make_prompt(owner, workspace_id=ws.id)
        pb_ws = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        ab_svc.create_experiment(
            owner, "Leaked?", suite_ws, pa_ws, 1, pb_ws, 2, workspace=ws
        )
        _db.session.commit()

        public_items = ab_svc.list_experiments(owner, workspace=None)
        for item in public_items:
            assert item.workspace_id is None


# ── ABSI-003 / ABSI-004 / ABSI-005 ───────────────────────────────────────────


class TestNonMemberRoutes:
    """Non-members of a workspace must not access its /w/<ws>/ab routes."""

    def _create_ws_experiment(self, owner, ws):
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()
        exp = ab_svc.create_experiment(
            owner, "WS-Route-Exp", suite, pa, 1, pb, 2, workspace=ws
        )
        _db.session.commit()
        return exp

    def test_non_member_cannot_get_ws_list(self, auth_client, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()
        _login(auth_client, outsider)
        resp = auth_client.get(f"/w/{ws.slug}/ab")
        assert resp.status_code in (403, 404)

    def test_non_member_cannot_get_ws_detail(self, auth_client, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        exp = self._create_ws_experiment(owner, ws)
        _login(auth_client, outsider)
        resp = auth_client.get(f"/w/{ws.slug}/ab/{exp.slug}")
        assert resp.status_code in (403, 404)

    def test_non_member_cannot_start_ws_experiment(self, auth_client, db_session):
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        exp = self._create_ws_experiment(owner, ws)
        _login(auth_client, outsider)
        resp = auth_client.post(f"/w/{ws.slug}/ab/{exp.slug}/start")
        assert resp.status_code in (403, 404)


# ── ABSI-006 ──────────────────────────────────────────────────────────────────


class TestCrossWorkspacePromptRejected:
    def test_workspace_b_prompt_rejected_for_workspace_a_experiment(self, db_session):
        owner_a = _make_user()
        owner_b = _make_user()
        ws_a = _make_workspace(owner_a)
        ws_b = _make_workspace(owner_b)
        _add_member(ws_b, owner_a, WorkspaceMemberRole.editor)
        suite_a = _make_suite(owner_a, workspace_id=ws_a.id)
        pa_a = _make_prompt(owner_a, workspace_id=ws_a.id)
        pb_b = _make_prompt(owner_b, workspace_id=ws_b.id)  # wrong workspace
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="different workspace"):
            ab_svc.create_experiment(
                owner_a, "CrossWS", suite_a, pa_a, 1, pb_b, 2, workspace=ws_a
            )


# ── ABSI-007 ──────────────────────────────────────────────────────────────────


class TestPublicPromptAllowedInWsExperiment:
    def test_public_prompt_allowed_in_workspace_experiment(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite = _make_suite(owner, workspace_id=ws.id)
        pub_a = _make_prompt(owner)  # public prompt
        pub_b = _make_prompt(owner)  # public prompt
        _db.session.commit()

        exp = ab_svc.create_experiment(
            owner, "PubPromptsInWS", suite, pub_a, 1, pub_b, 2, workspace=ws
        )
        _db.session.commit()
        assert exp.id is not None
        assert exp.workspace_id == ws.id


# ── ABSI-008 ──────────────────────────────────────────────────────────────────


class TestCacheControlHeader:
    def test_ws_ab_list_sets_private_no_store(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)  # owner already added inside _make_workspace
        _db.session.commit()
        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/ab")
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "no-store" in cc


# ── ABSI-009 / ABSI-010 ───────────────────────────────────────────────────────


class TestMemberRolePermissions:
    def test_viewer_cannot_create_experiment(self, db_session):
        owner = _make_user()
        viewer = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, viewer, WorkspaceMemberRole.viewer)
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        with pytest.raises(BenchmarkError, match="Editor role required"):
            ab_svc.create_experiment(
                viewer, "Viewer Exp", suite, pa, 1, pb, 2, workspace=ws
            )

    def test_editor_can_create_experiment(self, db_session):
        owner = _make_user()
        editor = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, editor, WorkspaceMemberRole.editor)
        suite = _make_suite(owner, workspace_id=ws.id)
        pa = _make_prompt(owner, workspace_id=ws.id)
        pb = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        exp = ab_svc.create_experiment(
            editor, "Editor Exp", suite, pa, 1, pb, 2, workspace=ws
        )
        _db.session.commit()
        assert exp.id is not None


# ── ABSI-011 ──────────────────────────────────────────────────────────────────


class TestPublicListRouteIsolation:
    def test_public_list_route_only_shows_public_experiments(
        self, auth_client, db_session
    ):
        owner = _make_user()
        ws = _make_workspace(owner)
        suite_pub = _make_suite(owner)
        suite_ws = _make_suite(owner, workspace_id=ws.id)
        pa_pub = _make_prompt(owner)
        pb_pub = _make_prompt(owner)
        pa_ws = _make_prompt(owner, workspace_id=ws.id)
        pb_ws = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        _pub_exp = ab_svc.create_experiment(
            owner, "Pub Route Exp", suite_pub, pa_pub, 1, pb_pub, 2
        )
        _ws_exp = ab_svc.create_experiment(
            owner, "WS Route Exp", suite_ws, pa_ws, 1, pb_ws, 2, workspace=ws
        )
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get("/ab")
        assert resp.status_code == 200
        # workspace experiment slug must not appear in public listing
        assert _ws_exp.slug.encode() not in resp.data
