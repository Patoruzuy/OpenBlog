"""Tests for public ontology browse routes.

Coverage
--------
  OBP-001  GET /ontology returns 200
  OBP-002  Non-public nodes not shown on public page
  OBP-003  GET /ontology/<slug> returns 200 with node info
  OBP-004  Prompts mapped via public mapping appear on node detail
  OBP-005  Prompts with workspace-only mapping NOT on public node detail
  OBP-006  Draft prompts NOT listed on node detail
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services import workspace_service as ws_svc
from backend.services.content_ontology_service import set_mappings
from backend.services.ontology_service import create_node

_ctr = itertools.count(22_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"obp{n}@example.com", f"obpuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_post(author, workspace_id=None, status=PostStatus.published, title=None):
    n = _n()
    post = Post(
        author_id=author.id,
        title=title or f"Post {n}",
        slug=f"post-obp-{n}",
        markdown_body="body",
        status=status,
        workspace_id=workspace_id,
        kind="prompt",
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"OBP WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _make_node(admin):
    n = _n()
    node = create_node(admin, f"obp-node-{n}", f"OBP Node {n}", is_public=True)
    _db.session.commit()
    return node


# ── OBP-001 ───────────────────────────────────────────────────────────────────


class TestPublicOntologyIndex:
    def test_returns_200(self, db_session, auth_client):
        """OBP-001"""
        resp = auth_client.get("/ontology")
        assert resp.status_code == 200

    def test_private_nodes_not_in_response(self, db_session, auth_client):
        """OBP-002"""
        admin = _make_user("admin")
        n = _n()
        priv_node = create_node(admin, f"priv-{n}", f"Private {n}", is_public=False)
        _db.session.commit()

        resp = auth_client.get("/ontology")
        assert resp.status_code == 200
        assert priv_node.name.encode() not in resp.data


# ── OBP-003 ───────────────────────────────────────────────────────────────────


class TestPublicNodeDetail:
    def test_existing_node_returns_200(self, db_session, auth_client):
        """OBP-003"""
        admin = _make_user("admin")
        node = _make_node(admin)

        resp = auth_client.get(f"/ontology/{node.slug}")
        assert resp.status_code == 200
        assert node.name.encode() in resp.data

    def test_mapped_published_prompt_visible(self, db_session, auth_client):
        """OBP-004"""
        admin = _make_user("admin")
        node = _make_node(admin)
        post = _make_post(admin, title="Mapped Prompt OBP")

        set_mappings(admin, post, [node.id], workspace=None)
        _db.session.commit()

        resp = auth_client.get(f"/ontology/{node.slug}")
        assert b"Mapped Prompt OBP" in resp.data

    def test_workspace_only_mapping_not_visible(self, db_session, auth_client):
        """OBP-005"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        post = _make_post(admin, title="WS Only Prompt OBP")

        set_mappings(owner, post, [node.id], workspace=ws)
        _db.session.commit()

        resp = auth_client.get(f"/ontology/{node.slug}")
        assert b"WS Only Prompt OBP" not in resp.data

    def test_draft_prompt_not_listed(self, db_session, auth_client):
        """OBP-006"""
        admin = _make_user("admin")
        node = _make_node(admin)
        draft = _make_post(admin, status=PostStatus.draft, title="Draft OBP Post")

        set_mappings(admin, draft, [node.id], workspace=None)
        _db.session.commit()

        resp = auth_client.get(f"/ontology/{node.slug}")
        assert b"Draft OBP Post" not in resp.data

    def test_unknown_slug_404(self, db_session, auth_client):
        resp = auth_client.get("/ontology/does-not-exist-xyz")
        assert resp.status_code == 404
