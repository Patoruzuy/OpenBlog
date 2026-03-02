"""Tests for Prompt Library route-level scope isolation.

Coverage
--------
  PRSI-001  GET /prompts/ only returns published public prompts.
  PRSI-002  GET /prompts/<slug> 404 for workspace prompt (public route).
  PRSI-003  GET /prompts/<slug> 404 for draft prompt (unauthenticated).
  PRSI-004  GET /w/<ws>/prompts/ 404 for non-member.
  PRSI-005  GET /w/<ws>/prompts/ 404 for unauthenticated user.
  PRSI-006  GET /w/<ws>/prompts/new 404 for viewer-role member (editor required).
  PRSI-007  Workspace prompt detail 404 via wrong workspace slug.
  PRSI-008  Workspace prompt NOT visible in public listing.
  PRSI-009  Public prompt NOT visible in workspace listing.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.post import PostStatus
from backend.models.workspace import WorkspaceMemberRole
from backend.services import prompt_service as svc
from backend.services import workspace_service as ws_svc

_ctr = itertools.count(100)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"si{n}@example.com", f"siuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


def _make_workspace(owner, name: str | None = None):
    n = _n()
    ws = ws_svc.create_workspace(name=name or f"SI WS {n}", owner=owner)
    _db.session.commit()
    return ws


# ── public list ───────────────────────────────────────────────────────────────


class TestPublicList:
    def test_public_list_shows_only_published_public(self, db_session, auth_client):
        """PRSI-001"""
        user = _make_user()
        owner = _make_user()
        ws = _make_workspace(owner)

        # Public published
        pub = svc.create_prompt(
            title="Pub Published",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        # Public draft – must NOT appear
        svc.create_prompt(
            title="Pub Draft",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.draft,
        )
        # Workspace prompt – must NOT appear
        svc.create_prompt(
            title="WS Prompt",
            markdown_body="body",
            author=user,
            workspace_id=ws.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/prompts/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert pub.title in html
        assert "Pub Draft" not in html
        assert "WS Prompt" not in html

    def test_workspace_prompt_absent_from_public_listing(self, db_session, auth_client):
        """PRSI-008"""
        owner = _make_user()
        ws = _make_workspace(owner)
        svc.create_prompt(
            title="Private WS Post",
            markdown_body="body",
            author=owner,
            workspace_id=ws.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/prompts/")
        html = resp.data.decode()
        assert "Private WS Post" not in html


# ── public detail ─────────────────────────────────────────────────────────────


class TestPublicDetail:
    def test_ws_prompt_via_public_route_is_404(self, db_session, auth_client):
        """PRSI-002"""
        owner = _make_user()
        ws = _make_workspace(owner)
        post = svc.create_prompt(
            title="WS Only Prompt",
            markdown_body="body",
            author=owner,
            workspace_id=ws.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get(f"/prompts/{post.slug}")
        assert resp.status_code == 404

    def test_draft_public_prompt_is_404_unauthenticated(self, db_session, auth_client):
        """PRSI-003"""
        user = _make_user()
        post = svc.create_prompt(
            title="Unpublished Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.draft,
        )
        _db.session.commit()

        resp = auth_client.get(f"/prompts/{post.slug}")
        assert resp.status_code == 404


# ── workspace routes ──────────────────────────────────────────────────────────


class TestWorkspaceRoutes:
    def test_ws_list_404_unauthenticated(self, db_session, auth_client):
        """PRSI-005"""
        owner = _make_user()
        ws = _make_workspace(owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/")
        assert resp.status_code == 404

    def test_ws_list_404_for_non_member(self, db_session, auth_client):
        """PRSI-004"""
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        _login(auth_client, outsider)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/")
        assert resp.status_code == 404

    def test_ws_new_404_for_viewer_role(self, db_session, auth_client):
        """PRSI-006"""
        owner = _make_user()
        viewer = _make_user()
        ws = _make_workspace(owner)
        ws_svc.add_member(ws, viewer, role=WorkspaceMemberRole.viewer)
        _db.session.commit()

        _login(auth_client, viewer)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/new")
        assert resp.status_code == 404

    def test_ws_prompt_detail_404_via_wrong_ws(self, db_session, auth_client):
        """PRSI-007"""
        owner = _make_user()
        ws1 = _make_workspace(owner, name="WS One")
        ws2 = _make_workspace(owner, name="WS Two")

        post = svc.create_prompt(
            title="WS1 Prompt",
            markdown_body="body",
            author=owner,
            workspace_id=ws1.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        # Access ws1 prompt via ws2 slug.
        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws2.slug}/prompts/{post.slug}")
        assert resp.status_code == 404

    def test_public_prompt_absent_from_workspace_listing(self, db_session, auth_client):
        """PRSI-009"""
        owner = _make_user()
        ws = _make_workspace(owner)

        svc.create_prompt(
            title="Actually Public Prompt",
            markdown_body="body",
            author=owner,
            workspace_id=None,  # public
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Actually Public Prompt" not in html

    def test_ws_member_can_see_ws_prompt_list(self, db_session, auth_client):
        """Sanity: editor can list workspace prompts."""
        owner = _make_user()
        ws = _make_workspace(owner)
        svc.create_prompt(
            title="Editor Visible",
            markdown_body="body",
            author=owner,
            workspace_id=ws.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/")
        assert resp.status_code == 200
        assert b"Editor Visible" in resp.data
