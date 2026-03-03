"""Tests for the workspace Intelligence Dashboard route.

Coverage
--------
  IW-001  Non-member gets 404 on workspace route.
  IW-002  Unauthenticated user gets 404 on workspace route.
  IW-003  Workspace member gets 200.
  IW-004  Workspace route sets Cache-Control: private, no-store.
  IW-005  Workspace prompt appears in workspace intelligence view.
  IW-006  Public prompt is also visible in workspace intelligence view.
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
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

_ctr = itertools.count(2000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"iw{n}@example.com",
        username=f"iwuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"IW-WS {n}", slug=f"iw-ws-{n}", owner_id=owner.id)
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


def _make_prompt(author, *, workspace_id=None):
    n = _n()
    p = Post(
        title=f"IW-Prompt {n}",
        slug=f"iw-prompt-{n}",
        kind="prompt",
        markdown_body="hello",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_benchmark_data(post, score: float, workspace_id=None):
    n = _n()
    dt = datetime.now(UTC) - timedelta(days=5)
    suite = BenchmarkSuite(
        name=f"IW-Suite {n}",
        slug=f"iw-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(suite)
    _db.session.flush()

    case_ = BenchmarkCase(
        suite_id=suite.id,
        name=f"IW-Case {n}",
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


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── IW-001 ─────────────────────────────────────────────────────────────────────


class TestNonMemberGets404:
    def test_non_member_is_rejected(self, auth_client, db_session):
        owner = _make_user()
        visitor = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()

        _login(auth_client, visitor)
        resp = auth_client.get(f"/w/{ws.slug}/intelligence")
        assert resp.status_code == 404


# ── IW-002 ─────────────────────────────────────────────────────────────────────


class TestUnauthenticatedGets404:
    def test_unauthenticated_is_rejected(self, client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()

        resp = client.get(f"/w/{ws.slug}/intelligence")
        assert resp.status_code == 404


# ── IW-003 ─────────────────────────────────────────────────────────────────────


class TestMemberGets200:
    def test_owner_gets_200(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/intelligence")
        assert resp.status_code == 200

    def test_viewer_member_gets_200(self, auth_client, db_session):
        owner = _make_user()
        viewer = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        _login(auth_client, viewer)
        resp = auth_client.get(f"/w/{ws.slug}/intelligence")
        assert resp.status_code == 200


# ── IW-004 ─────────────────────────────────────────────────────────────────────


class TestWorkspaceCacheHeader:
    def test_private_no_store_header_on_ws_route(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/intelligence")
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "no-store" in cc


# ── IW-005 ─────────────────────────────────────────────────────────────────────


class TestWorkspacePromptVisible:
    def test_ws_prompt_appears_in_ws_view(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws.id)
        _make_benchmark_data(prompt, score=0.88, workspace_id=ws.id)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/intelligence")
        assert resp.status_code == 200
        assert prompt.title.encode() in resp.data


# ── IW-006 ─────────────────────────────────────────────────────────────────────


class TestPublicPromptVisibleInWorkspaceView:
    def test_public_prompt_appears_in_ws_view(self, auth_client, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        pub_prompt = _make_prompt(owner, workspace_id=None)
        _make_benchmark_data(pub_prompt, score=0.77, workspace_id=None)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/intelligence")
        assert resp.status_code == 200
        assert pub_prompt.title.encode() in resp.data
