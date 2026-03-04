"""Tests for Fork Recommendation Service — workspace scope.

Coverage
--------
  FRWS-001  compute_family (workspace scope) includes public + same-workspace forks.
  FRWS-002  Forks from a DIFFERENT workspace are excluded.
  FRWS-003  GET /w/<ws>/prompts/<slug>/recommendations returns 200 for member.
  FRWS-004  Response carries Cache-Control: private, no-store.
  FRWS-005  Non-member → 404 (fail-closed via get_workspace_for_user).
  FRWS-006  Workspace forks appear with scope='workspace' in recommendations.
  FRWS-007  recommend() in workspace scope returns [] when no forks exist.
  FRWS-008  Unauthenticated GET on workspace route → 404 (workspace_for_user guard).
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import fork_recommendation_service as svc

_ctr = itertools.count(11_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"frws{n}@example.com",
        username=f"frwsuser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"FRWS-WS {n}", slug=f"frws-ws-{n}", owner_id=owner.id)
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
        title=f"FRWS-Prompt {n}",
        slug=f"frws-prompt-{n}",
        kind="prompt",
        markdown_body="hello {{name}}",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_fork(
    base: Post, author, *, workspace_id=None, status=PostStatus.published
) -> Post:
    n = _n()
    fork = Post(
        title=f"FRWS-Fork {n}",
        slug=f"frws-fork-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(fork)
    _db.session.flush()
    _db.session.add(
        ContentLink(
            from_post_id=fork.id,
            to_post_id=base.id,
            link_type="derived_from",
            created_by_user_id=author.id,
        )
    )
    _db.session.flush()
    return fork


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ==============================================================================
# FRWS-001 — workspace scope includes public + same-ws forks
# ==============================================================================


class TestComputeFamilyWorkspaceScope:
    def test_includes_public_and_same_workspace_forks(self, db_session):
        """FRWS-001"""
        owner = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        pub_fork = _make_fork(base, owner)  # public fork
        ws_fork = _make_fork(base, owner, workspace_id=ws.id)  # same-ws fork
        _db.session.commit()

        family = svc.compute_family(base, workspace=ws)
        ids = {f.id for f in family}
        assert pub_fork.id in ids
        assert ws_fork.id in ids

    def test_excludes_other_workspace_forks(self, db_session):
        """FRWS-002"""
        owner = _make_user()
        ws = _make_workspace(owner)
        other_ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _same_fork = _make_fork(base, owner, workspace_id=ws.id)
        other_ws_fork = _make_fork(base, owner, workspace_id=other_ws.id)
        _db.session.commit()

        family = svc.compute_family(base, workspace=ws)
        assert other_ws_fork.id not in {f.id for f in family}


# ==============================================================================
# FRWS-006 — workspace fork carries scope='workspace'
# ==============================================================================


class TestWorkspaceScopeBadge:
    def test_workspace_forks_carry_workspace_scope(self, db_session):
        """FRWS-006"""
        owner = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _make_fork(base, owner, workspace_id=ws.id)
        _db.session.commit()

        recs = svc.recommend(owner, base, workspace=ws)
        # All workspace-scoped forks should carry scope='workspace'
        ws_recs = [r for r in recs if r.workspace_id is not None]
        assert all(r.scope == "workspace" for r in ws_recs)

    def test_public_forks_carry_public_scope_in_ws_call(self, db_session):
        owner = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _make_fork(base, owner)  # public fork
        _db.session.commit()

        recs = svc.recommend(owner, base, workspace=ws)
        pub_recs = [r for r in recs if r.workspace_id is None]
        assert all(r.scope == "public" for r in pub_recs)


# ==============================================================================
# FRWS-007 — empty family returns []
# ==============================================================================


class TestEmptyWorkspaceFamily:
    def test_empty_family_returns_empty_list(self, db_session):
        """FRWS-007"""
        owner = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        recs = svc.recommend(owner, base, workspace=ws)
        assert recs == []


# ==============================================================================
# FRWS-003 / FRWS-004 / FRWS-005 / FRWS-008 — HTTP routes
# ==============================================================================


class TestWorkspaceRoute:
    def test_member_gets_200(self, db_session, auth_client):
        """FRWS-003"""
        owner = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _make_fork(base, owner, workspace_id=ws.id)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{base.slug}/recommendations")
        assert resp.status_code == 200

    def test_cache_control_header_present(self, db_session, auth_client):
        """FRWS-004"""
        owner = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{base.slug}/recommendations")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert "private" in cc

    def test_non_member_gets_404(self, db_session, auth_client):
        """FRWS-005"""
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        _login(auth_client, outsider)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{base.slug}/recommendations")
        assert resp.status_code == 404

    def test_unauthenticated_gets_404(self, db_session, auth_client):
        """FRWS-008: workspace_for_user aborts 404 for anonymous users."""
        owner = _make_user()
        ws = _make_workspace(owner)
        base = _make_prompt(owner, workspace_id=ws.id)
        _db.session.commit()

        resp = auth_client.get(f"/w/{ws.slug}/prompts/{base.slug}/recommendations")
        assert resp.status_code == 404
