"""Tests for ontology node admin service and routes.

Coverage
--------
  ONA-001  create_node by admin — success
  ONA-002  create_node by editor — success
  ONA-003  create_node by contributor — OntologyError 403
  ONA-004  duplicate slug raises OntologyError 400
  ONA-005  parent_id stored correctly
  ONA-006  update_node changes fields
  ONA-007  list_tree(public_only=True) returns only public nodes
  ONA-008  list_tree(public_only=False) returns all nodes
  ONA-009  tree is nested correctly (children under parent)
  ONA-010  GET /admin/ontology returns 200 for admin
  ONA-011  Non-admin is redirected from admin ontology route
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.services.ontology_service import (
    OntologyError,
    create_node,
    list_tree,
    update_node,
)

_ctr = itertools.count(20_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"ona{n}@example.com", f"onauser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── ONA-001 / ONA-002 / ONA-003 ──────────────────────────────────────────────


class TestCreateNode:
    def test_admin_can_create(self, db_session):
        """ONA-001"""
        admin = _make_user("admin")
        n = _n()
        node = create_node(admin, f"concept-{n}", f"Concept {n}")
        _db.session.commit()
        assert node.id is not None
        assert node.slug == f"concept-{n}"
        assert node.created_by_user_id == admin.id

    def test_editor_can_create(self, db_session):
        """ONA-002"""
        editor = _make_user("editor")
        n = _n()
        node = create_node(editor, f"ed-concept-{n}", f"Ed Concept {n}")
        _db.session.commit()
        assert node.id is not None

    def test_contributor_cannot_create(self, db_session):
        """ONA-003"""
        contrib = _make_user("contributor")
        with pytest.raises(OntologyError) as exc:
            create_node(contrib, "no-perm", "No Permission")
        assert exc.value.status_code == 403

    def test_duplicate_slug_raises(self, db_session):
        """ONA-004"""
        admin = _make_user("admin")
        n = _n()
        create_node(admin, f"dup-{n}", f"Dup {n}")
        _db.session.commit()
        with pytest.raises(OntologyError) as exc:
            create_node(admin, f"dup-{n}", "Different Name")
        assert exc.value.status_code == 409

    def test_parent_id_stored(self, db_session):
        """ONA-005"""
        admin = _make_user("admin")
        n = _n()
        parent = create_node(admin, f"parent-{n}", f"Parent {n}")
        _db.session.commit()
        child = create_node(admin, f"child-{n}", f"Child {n}", parent_id=parent.id)
        _db.session.commit()
        assert child.parent_id == parent.id


# ── ONA-006 ───────────────────────────────────────────────────────────────────


class TestUpdateNode:
    def test_update_changes_name_and_description(self, db_session):
        """ONA-006"""
        admin = _make_user("admin")
        n = _n()
        node = create_node(admin, f"upd-{n}", f"Upd {n}")
        _db.session.commit()

        updated = update_node(admin, node.id, name="New Name", description="Desc")
        _db.session.commit()
        assert updated.name == "New Name"
        assert updated.description == "Desc"

    def test_update_is_public(self, db_session):
        admin = _make_user("admin")
        n = _n()
        node = create_node(admin, f"pub-toggle-{n}", f"PubToggle {n}", is_public=True)
        _db.session.commit()

        updated = update_node(admin, node.id, is_public=False)
        _db.session.commit()
        assert updated.is_public is False


# ── ONA-007 / ONA-008 / ONA-009 ──────────────────────────────────────────────


class TestListTree:
    def test_public_only_excludes_private(self, db_session):
        """ONA-007"""
        admin = _make_user("admin")
        n = _n()
        create_node(admin, f"pub-node-{n}", f"Pub {n}", is_public=True)
        create_node(admin, f"priv-node-{n}", f"Priv {n}", is_public=False)
        _db.session.commit()

        slugs = {item.node.slug for item in list_tree(public_only=True)}
        assert f"pub-node-{n}" in slugs
        assert f"priv-node-{n}" not in slugs

    def test_public_only_false_includes_private(self, db_session):
        """ONA-008"""
        admin = _make_user("admin")
        n = _n()
        create_node(admin, f"all-pub-{n}", f"AllPub {n}", is_public=True)
        create_node(admin, f"all-priv-{n}", f"AllPriv {n}", is_public=False)
        _db.session.commit()

        slugs = {item.node.slug for item in list_tree(public_only=False)}
        assert f"all-pub-{n}" in slugs
        assert f"all-priv-{n}" in slugs

    def test_tree_is_nested(self, db_session):
        """ONA-009"""
        admin = _make_user("admin")
        n = _n()
        parent = create_node(admin, f"tree-parent-{n}", f"TreeParent {n}")
        _db.session.commit()
        create_node(admin, f"tree-child-{n}", f"TreeChild {n}", parent_id=parent.id)
        _db.session.commit()

        tree = list_tree(public_only=True)
        parent_item = next(
            (i for i in tree if i.node.slug == f"tree-parent-{n}"), None
        )
        assert parent_item is not None
        child_slugs = {c.node.slug for c in parent_item.children}
        assert f"tree-child-{n}" in child_slugs


# ── ONA-010 / ONA-011 ─────────────────────────────────────────────────────────


class TestAdminOntologyRoutes:
    def test_admin_can_access_list(self, db_session, auth_client):
        """ONA-010"""
        admin = _make_user("admin")
        _login(auth_client, admin)
        resp = auth_client.get("/admin/ontology")
        assert resp.status_code == 200

    def test_non_admin_redirected(self, db_session, auth_client):
        """ONA-011"""
        reader = _make_user("reader")
        _login(auth_client, reader)
        resp = auth_client.get("/admin/ontology")
        # require_admin_access redirects non-admins
        assert resp.status_code in (302, 403)
