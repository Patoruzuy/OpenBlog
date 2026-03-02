"""Tests for filtering prompts by ontology on browse pages.

Coverage
--------
  PFO-001  Mapped published prompt visible on node detail page
  PFO-002  Descendant-node prompts visible via include_descendants
  PFO-003  Prompts not mapped to that node excluded
  PFO-004  Workspace prompts NOT visible on public node detail
  PFO-005  GET /w/<ws>/ontology/<slug> includes overlay-mapped prompts
  PFO-006  Unknown ontology slug returns 404 (node not found)
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services import workspace_service as ws_svc
from backend.services.content_ontology_service import set_mappings
from backend.services.ontology_service import create_node

_ctr = itertools.count(24_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"pfo{n}@example.com", f"pfouser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"PFO WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _make_post(author, workspace_id=None, status=PostStatus.published, title=None):
    n = _n()
    post = Post(
        author_id=author.id,
        title=title or f"Post {n}",
        slug=f"post-pfo-{n}",
        markdown_body="body",
        status=status,
        workspace_id=workspace_id,
        kind="prompt",
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_node(admin, parent_id=None):
    n = _n()
    node = create_node(
        admin, f"pfo-node-{n}", f"PFO Node {n}", is_public=True, parent_id=parent_id
    )
    _db.session.commit()
    return node


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── PFO-001 ───────────────────────────────────────────────────────────────────


class TestPublicFilter:
    def test_mapped_prompt_visible(self, db_session, auth_client):
        """PFO-001"""
        admin = _make_user("admin")
        node = _make_node(admin)
        post = _make_post(admin, title="Mapped PFO Prompt")

        set_mappings(admin, post, [node.id])
        _db.session.commit()

        resp = auth_client.get(f"/ontology/{node.slug}")
        assert resp.status_code == 200
        assert b"Mapped PFO Prompt" in resp.data

    def test_descendant_prompt_visible(self, db_session, auth_client):
        """PFO-002"""
        admin = _make_user("admin")
        parent_node = _make_node(admin)
        child_node = _make_node(admin, parent_id=parent_node.id)
        post = _make_post(admin, title="Child Node Prompt PFO")

        set_mappings(admin, post, [child_node.id])
        _db.session.commit()

        # Viewing parent page should include child-mapped prompts (include_descendants=True)
        resp = auth_client.get(f"/ontology/{parent_node.slug}")
        assert resp.status_code == 200
        assert b"Child Node Prompt PFO" in resp.data

    def test_unrelated_prompt_excluded(self, db_session, auth_client):
        """PFO-003"""
        admin = _make_user("admin")
        node_a = _make_node(admin)
        _make_node(admin)  # node_b — different node
        post_a = _make_post(admin, title="Post A PFO")
        _make_post(admin, title="Post B PFO")  # not mapped to node_a

        set_mappings(admin, post_a, [node_a.id])
        _db.session.commit()
        # post_b is not mapped to node_a

        resp = auth_client.get(f"/ontology/{node_a.slug}")
        assert b"Post A PFO" in resp.data
        assert b"Post B PFO" not in resp.data

    def test_workspace_prompt_excluded_from_public(self, db_session, auth_client):
        """PFO-004"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        ws_post = _make_post(admin, workspace_id=ws.id, title="WS Prompt PFO")

        set_mappings(admin, ws_post, [node.id])
        _db.session.commit()

        resp = auth_client.get(f"/ontology/{node.slug}")
        assert b"WS Prompt PFO" not in resp.data


# ── PFO-005 / PFO-006 ─────────────────────────────────────────────────────────


class TestWorkspaceFilter:
    def test_overlay_mapped_prompt_visible(self, db_session, auth_client):
        """PFO-005"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        post = _make_post(admin, title="Overlay Prompt PFO")

        set_mappings(owner, post, [node.id], workspace=ws)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}")
        assert resp.status_code == 200
        assert b"Overlay Prompt PFO" in resp.data

    def test_unknown_node_slug_404(self, db_session, auth_client):
        """PFO-006"""
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        _login(auth_client, owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/does-not-exist-pfo")
        assert resp.status_code == 404
