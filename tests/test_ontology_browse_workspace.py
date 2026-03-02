"""Tests for workspace ontology browse routes.

Coverage
--------
  OBW-001  GET /w/<ws>/ontology returns 200 for member
  OBW-002  Non-member gets 404
  OBW-003  Workspace overlay mappings visible to member
  OBW-004  Other-workspace mappings NOT visible
  OBW-005  Response carries Cache-Control: private, no-store
  OBW-006  Unauthenticated user gets 404
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.workspace import WorkspaceMember, WorkspaceMemberRole
from backend.services import workspace_service as ws_svc
from backend.services.content_ontology_service import set_mappings
from backend.services.ontology_service import create_node

_ctr = itertools.count(23_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"obw{n}@example.com", f"obwuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"OBW WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    _db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    _db.session.commit()


def _make_post(author, workspace_id=None, status=PostStatus.published, title=None):
    n = _n()
    post = Post(
        author_id=author.id,
        title=title or f"Post {n}",
        slug=f"post-obw-{n}",
        markdown_body="body",
        status=status,
        workspace_id=workspace_id,
        kind="prompt",
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_node(admin):
    n = _n()
    node = create_node(admin, f"obw-node-{n}", f"OBW Node {n}", is_public=True)
    _db.session.commit()
    return node


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── OBW-001 ───────────────────────────────────────────────────────────────────


class TestWSOntologyIndex:
    def test_member_gets_200(self, db_session, auth_client):
        """OBW-001"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        _login(auth_client, owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology")
        assert resp.status_code == 200

    def test_non_member_gets_404(self, db_session, auth_client):
        """OBW-002"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        outsider = _make_user()
        _login(auth_client, outsider)

        resp = auth_client.get(f"/w/{ws.slug}/ontology")
        assert resp.status_code == 404

    def test_unauthenticated_gets_404(self, db_session, auth_client):
        """OBW-006"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology")
        assert resp.status_code == 404

    def test_cache_control_header(self, db_session, auth_client):
        """OBW-005"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        _login(auth_client, owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "no-store" in cc


# ── OBW-003 / OBW-004 ─────────────────────────────────────────────────────────


class TestWSOntologyNodeDetail:
    def test_ws_overlay_mapping_visible_to_member(self, db_session, auth_client):
        """OBW-003"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        post = _make_post(admin, title="WS Member Prompt OBW")

        set_mappings(owner, post, [node.id], workspace=ws)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}")
        assert resp.status_code == 200
        assert b"WS Member Prompt OBW" in resp.data

    def test_other_ws_mappings_not_visible(self, db_session, auth_client):
        """OBW-004"""
        admin = _make_user("admin")
        owner1 = _make_user("editor")
        owner2 = _make_user("editor")
        ws1 = _make_workspace(owner1)
        ws2 = _make_workspace(owner2)
        node = _make_node(admin)
        post = _make_post(admin, title="WS1 Only Prompt OBW")

        set_mappings(owner1, post, [node.id], workspace=ws1)
        _db.session.commit()

        # Owner2 views ws2's node page — should not see ws1's mapping
        _login(auth_client, owner2)
        resp = auth_client.get(f"/w/{ws2.slug}/ontology/{node.slug}")
        assert resp.status_code == 200
        assert b"WS1 Only Prompt OBW" not in resp.data
