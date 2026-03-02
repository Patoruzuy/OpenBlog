"""Scope isolation tests for the Knowledge Graph / ContentLink feature.

Coverage
--------
  CLSI-001  public → public link: allowed.
  CLSI-002  public → workspace link: FORBIDDEN (ContentLinkError 400).
  CLSI-003  workspace → same workspace link: allowed.
  CLSI-004  workspace → public link: allowed.
  CLSI-005  workspace A → workspace B link: FORBIDDEN.
  CLSI-006  list_links_for_post(workspace_id=None) never returns workspace links.
  CLSI-007  list_links_for_post(workspace_id=ws.id) never returns public links.
  CLSI-008  Workspace viewer cannot add_link (ContentLinkError 403).
  CLSI-009  Workspace editor can add_link in workspace scope.
  CLSI-010  Workspace link not returned when querying public scope.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.workspace import WorkspaceMemberRole
from backend.services import content_link_service as svc
from backend.services import workspace_service as ws_svc

_ctr = itertools.count(100)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.services.auth_service import AuthService
    from backend.models.user import UserRole

    n = _n()
    user = AuthService.register(
        f"si{n}@example.com", f"siuser{n}", "StrongPass123!!"
    )
    if role != "reader":
        user.role = UserRole(role)
        _db.session.flush()
    return user


def _make_workspace(owner, name: str | None = None):
    n = _n()
    ws = ws_svc.create_workspace(name=name or f"WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _make_post(author, workspace_id=None, kind="article"):
    n = _n()
    p = Post(
        title=f"WS-Post {n}",
        slug=f"ws-post-{n}",
        kind=kind,
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


# ── Direction rules ───────────────────────────────────────────────────────────


class TestDirectionRules:
    def test_public_to_public_allowed(self, db_session):
        """CLSI-001"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _db.session.commit()

        link = svc.add_link(editor, p1, p2, "related")
        _db.session.commit()
        assert link.workspace_id is None

    def test_public_to_workspace_forbidden(self, db_session):
        """CLSI-002"""
        editor = _make_user("editor")
        owner = _make_user()
        ws = _make_workspace(owner)

        pub_post = _make_post(editor)
        ws_post = _make_post(owner, workspace_id=ws.id)
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.add_link(editor, pub_post, ws_post, "related")
        assert exc.value.status_code == 400

    def test_workspace_to_same_workspace_allowed(self, db_session):
        """CLSI-003"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)

        p1 = _make_post(owner, workspace_id=ws.id)
        p2 = _make_post(owner, workspace_id=ws.id)
        _db.session.commit()

        link = svc.add_link(owner, p1, p2, "implements")
        _db.session.commit()
        assert link.workspace_id == ws.id

    def test_workspace_to_public_allowed(self, db_session):
        """CLSI-004"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)

        ws_post = _make_post(owner, workspace_id=ws.id)
        pub_post = _make_post(owner)  # public
        _db.session.commit()

        link = svc.add_link(owner, ws_post, pub_post, "inspired_by")
        _db.session.commit()
        # Link is scoped to the workspace (from_post's scope).
        assert link.workspace_id == ws.id

    def test_workspace_a_to_workspace_b_forbidden(self, db_session):
        """CLSI-005"""
        owner = _make_user("editor")
        ws1 = _make_workspace(owner, name="WS-A")
        ws2 = _make_workspace(owner, name="WS-B")

        p1 = _make_post(owner, workspace_id=ws1.id)
        p2 = _make_post(owner, workspace_id=ws2.id)
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.add_link(owner, p1, p2, "related")
        assert exc.value.status_code == 400


# ── Query scope isolation ─────────────────────────────────────────────────────


class TestQueryScopeIsolation:
    def test_public_query_excludes_workspace_links(self, db_session):
        """CLSI-006"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)

        hub_public = _make_post(owner)
        target_public = _make_post(owner)

        hub_ws = _make_post(owner, workspace_id=ws.id)
        target_ws = _make_post(owner, workspace_id=ws.id)
        # Also add a workspace→public link; that is also workspace-scoped.
        target_pub2 = _make_post(owner)
        _db.session.commit()

        # Public link
        svc.add_link(owner, hub_public, target_public, "related")
        # Workspace-scoped links
        svc.add_link(owner, hub_ws, target_ws, "implements")
        svc.add_link(owner, hub_ws, target_pub2, "inspired_by")
        _db.session.commit()

        pub_links = svc.list_links_for_post(hub_public, workspace_id=None)
        assert all(lnk.workspace_id is None for lnk in pub_links)
        # workspace links must not appear for hub_public when queried with None
        ws_links = svc.list_links_for_post(hub_ws, workspace_id=None)
        assert ws_links == []

    def test_workspace_query_excludes_public_links(self, db_session):
        """CLSI-007"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)

        hub_ws = _make_post(owner, workspace_id=ws.id)
        spoke_ws = _make_post(owner, workspace_id=ws.id)
        hub_pub = _make_post(owner)
        spoke_pub = _make_post(owner)
        _db.session.commit()

        svc.add_link(owner, hub_ws, spoke_ws, "related")  # ws-scoped
        svc.add_link(owner, hub_pub, spoke_pub, "related")  # public
        _db.session.commit()

        ws_links = svc.list_links_for_post(hub_ws, workspace_id=ws.id)
        assert all(lnk.workspace_id == ws.id for lnk in ws_links)

        pub_links = svc.list_links_for_post(hub_pub, workspace_id=ws.id)
        assert pub_links == []


# ── Workspace member permissions ──────────────────────────────────────────────


class TestWorkspaceMemberPermissions:
    def test_viewer_cannot_add_link(self, db_session):
        """CLSI-008"""
        owner = _make_user()
        viewer = _make_user()
        ws = _make_workspace(owner)
        ws_svc.add_member(ws, viewer, role=WorkspaceMemberRole.viewer)
        _db.session.commit()

        p1 = _make_post(owner, workspace_id=ws.id)
        p2 = _make_post(owner, workspace_id=ws.id)
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.add_link(viewer, p1, p2, "related")
        assert exc.value.status_code == 403

    def test_workspace_editor_can_add_link(self, db_session):
        """CLSI-009"""
        owner = _make_user()
        wsedit = _make_user()
        ws = _make_workspace(owner)
        ws_svc.add_member(ws, wsedit, role=WorkspaceMemberRole.editor)
        _db.session.commit()

        p1 = _make_post(owner, workspace_id=ws.id)
        p2 = _make_post(owner, workspace_id=ws.id)
        _db.session.commit()

        link = svc.add_link(wsedit, p1, p2, "related")
        _db.session.commit()
        assert link.workspace_id == ws.id

    def test_workspace_link_absent_from_public_query(self, db_session):
        """CLSI-010"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)

        p1 = _make_post(owner, workspace_id=ws.id)
        p2_pub = _make_post(owner)  # public target
        _db.session.commit()

        # ws→public link is workspace-scoped; must NOT appear when queried public.
        svc.add_link(owner, p1, p2_pub, "inspired_by")
        _db.session.commit()

        # Querying p2_pub's incoming links in PUBLIC scope.
        pub_incoming = svc.list_links_for_post(p2_pub, workspace_id=None, direction="incoming")
        # These are all workspace-scoped, so should not appear with workspace_id=None.
        assert all(lnk.workspace_id is None for lnk in pub_incoming)
