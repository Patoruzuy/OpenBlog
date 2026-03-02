"""Tests for content ontology mapping scope isolation.

Coverage
--------
  COM-001  set_mappings public prompt → creates workspace_id=NULL rows
  COM-002  set_mappings workspace prompt → creates workspace_id=ws rows
  COM-003  set_mappings replaces existing (delete-then-insert)
  COM-004  contributor cannot set_mappings on public prompt (403)
  COM-005  get_mappings_for_post public scope returns only workspace_id IS NULL
  COM-006  get_mappings_for_post workspace scope returns public + overlay
  COM-007  other-workspace mappings NOT in get_mappings_for_post
  COM-008  get_mapping_ids_for_post returns list of int node IDs
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services import workspace_service as ws_svc
from backend.services.content_ontology_service import (
    ContentOntologyError,
    get_mapping_ids_for_post,
    get_mappings_for_post,
    set_mappings,
)
from backend.services.ontology_service import create_node

_ctr = itertools.count(21_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"com{n}@example.com", f"comuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"COM WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _make_post(author, workspace_id=None, status=PostStatus.published):
    n = _n()
    post = Post(
        author_id=author.id,
        title=f"Post {n}",
        slug=f"post-com-{n}",
        markdown_body="body",
        status=status,
        workspace_id=workspace_id,
        kind="prompt",
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _make_node(admin, pub=True):
    n = _n()
    node = create_node(admin, f"com-node-{n}", f"COM Node {n}", is_public=pub)
    _db.session.commit()
    return node


# ── COM-001 ───────────────────────────────────────────────────────────────────


class TestSetMappingsPublic:
    def test_creates_public_scope_rows(self, db_session):
        """COM-001"""
        admin = _make_user("admin")
        post = _make_post(admin)
        node = _make_node(admin)

        set_mappings(admin, post, [node.id], workspace=None)
        _db.session.commit()

        rows = get_mappings_for_post(admin, post, workspace=None)
        assert len(rows) == 1
        assert rows[0].workspace_id is None
        assert rows[0].ontology_node_id == node.id

    def test_contributor_cannot_map(self, db_session):
        """COM-004"""
        admin = _make_user("admin")
        contrib = _make_user("contributor")
        post = _make_post(admin)
        node = _make_node(admin)

        with pytest.raises(ContentOntologyError) as exc:
            set_mappings(contrib, post, [node.id], workspace=None)
        assert exc.value.status_code == 403


# ── COM-002 ───────────────────────────────────────────────────────────────────


class TestSetMappingsWorkspace:
    def test_creates_workspace_scope_rows(self, db_session):
        """COM-002"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        post = _make_post(owner, workspace_id=ws.id)
        node = _make_node(admin)

        set_mappings(owner, post, [node.id], workspace=ws)
        _db.session.commit()

        rows = get_mappings_for_post(owner, post, workspace=ws)
        ws_rows = [r for r in rows if r.workspace_id == ws.id]
        assert len(ws_rows) == 1
        assert ws_rows[0].ontology_node_id == node.id


# ── COM-003 ───────────────────────────────────────────────────────────────────


class TestReplaceMapping:
    def test_set_mappings_replaces_existing(self, db_session):
        """COM-003"""
        admin = _make_user("admin")
        post = _make_post(admin)
        node1 = _make_node(admin)
        node2 = _make_node(admin)

        set_mappings(admin, post, [node1.id], workspace=None)
        _db.session.commit()

        set_mappings(admin, post, [node2.id], workspace=None)
        _db.session.commit()

        rows = get_mappings_for_post(admin, post, workspace=None)
        node_ids = {r.ontology_node_id for r in rows}
        assert node1.id not in node_ids
        assert node2.id in node_ids

    def test_set_empty_clears_all(self, db_session):
        admin = _make_user("admin")
        post = _make_post(admin)
        node = _make_node(admin)

        set_mappings(admin, post, [node.id])
        _db.session.commit()
        set_mappings(admin, post, [])
        _db.session.commit()

        assert get_mappings_for_post(admin, post) == []


# ── COM-005 / COM-006 / COM-007 ───────────────────────────────────────────────


class TestGetMappingsScope:
    def test_public_scope_excludes_ws_rows(self, db_session):
        """COM-005"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        post = _make_post(admin)
        node = _make_node(admin)

        # Only workspace overlay
        set_mappings(owner, post, [node.id], workspace=ws)
        _db.session.commit()

        public_rows = get_mappings_for_post(admin, post, workspace=None)
        assert all(r.workspace_id is None for r in public_rows)
        # No public mapping was set, so result is empty
        assert public_rows == []

    def test_workspace_scope_includes_public_plus_overlay(self, db_session):
        """COM-006"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        post = _make_post(admin)
        node_pub = _make_node(admin)
        node_ws = _make_node(admin)

        set_mappings(admin, post, [node_pub.id], workspace=None)
        _db.session.commit()
        set_mappings(owner, post, [node_ws.id], workspace=ws)
        _db.session.commit()

        rows = get_mappings_for_post(owner, post, workspace=ws)
        node_ids = {r.ontology_node_id for r in rows}
        assert node_pub.id in node_ids
        assert node_ws.id in node_ids

    def test_other_workspace_rows_not_visible(self, db_session):
        """COM-007"""
        admin = _make_user("admin")
        owner1 = _make_user("editor")
        owner2 = _make_user("editor")
        ws1 = _make_workspace(owner1)
        ws2 = _make_workspace(owner2)
        post = _make_post(admin)
        node = _make_node(admin)

        set_mappings(owner1, post, [node.id], workspace=ws1)
        _db.session.commit()

        # ws2 scope should NOT see ws1 mapping
        rows = get_mappings_for_post(owner2, post, workspace=ws2)
        ws1_rows = [r for r in rows if r.workspace_id == ws1.id]
        assert ws1_rows == []


# ── COM-008 ───────────────────────────────────────────────────────────────────


class TestGetMappingIds:
    def test_returns_list_of_ints(self, db_session):
        """COM-008"""
        admin = _make_user("admin")
        post = _make_post(admin)
        node = _make_node(admin)

        set_mappings(admin, post, [node.id])
        _db.session.commit()

        ids = get_mapping_ids_for_post(post)
        assert isinstance(ids, list)
        assert node.id in ids
