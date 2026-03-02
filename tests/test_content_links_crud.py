"""Service-layer CRUD tests for the Knowledge Graph / ContentLink feature.

Coverage
--------
  CL-001  add_link creates a ContentLink row.
  CL-002  add_link raises 409 for duplicate (same from/to/type/workspace).
  CL-003  add_link raises 400 for invalid link_type.
  CL-004  add_link raises 400 for self-links.
  CL-005  add_link raises 403 when caller is a plain reader (public scope).
  CL-006  remove_link deletes the row successfully.
  CL-007  remove_link raises 404 for unknown link_id.
  CL-008  list_links_for_post direction=outgoing returns outgoing only.
  CL-009  list_links_for_post direction=incoming returns incoming only.
  CL-010  list_links_grouped returns correct outgoing/incoming dicts.
  CL-011  get_link_or_none returns None for unknown id.
  CL-012  Cascade delete: deleting a post removes its ContentLink rows.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.services import content_link_service as svc

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(
        f"cl{n}@example.com", f"cluser{n}", "StrongPass123!!"
    )
    if role != "reader":
        user.role = UserRole(role)
        _db.session.flush()
    return user


def _make_post(author, workspace_id=None, kind="article"):
    n = _n()
    p = Post(
        title=f"Post {n}",
        slug=f"post-{n}",
        kind=kind,
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


# ── Add link ──────────────────────────────────────────────────────────────────


class TestAddLink:
    def test_creates_row(self, db_session):
        """CL-001"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _db.session.commit()

        link = svc.add_link(editor, p1, p2, "related")
        _db.session.commit()

        assert link.id is not None
        assert link.from_post_id == p1.id
        assert link.to_post_id == p2.id
        assert link.link_type == "related"
        assert link.workspace_id is None
        assert link.created_by_user_id == editor.id

    def test_duplicate_raises_409(self, db_session):
        """CL-002"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _db.session.commit()

        svc.add_link(editor, p1, p2, "related")
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.add_link(editor, p1, p2, "related")
        assert exc.value.status_code == 409

    def test_invalid_link_type_raises_400(self, db_session):
        """CL-003"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.add_link(editor, p1, p2, "knows")
        assert exc.value.status_code == 400

    def test_self_link_raises_400(self, db_session):
        """CL-004"""
        editor = _make_user("editor")
        p = _make_post(editor)
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.add_link(editor, p, p, "related")
        assert exc.value.status_code == 400

    def test_reader_cannot_add_link(self, db_session):
        """CL-005: plain reader cannot create links in public scope."""
        reader = _make_user("reader")
        p1 = _make_post(reader)
        p2 = _make_post(reader)
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.add_link(reader, p1, p2, "related")
        assert exc.value.status_code == 403


# ── Remove link ───────────────────────────────────────────────────────────────


class TestRemoveLink:
    def test_remove_deletes_row(self, db_session):
        """CL-006"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _db.session.commit()

        link = svc.add_link(editor, p1, p2, "related")
        _db.session.commit()
        link_id = link.id

        svc.remove_link(editor, link_id)
        _db.session.commit()

        assert _db.session.get(ContentLink, link_id) is None

    def test_remove_unknown_raises_404(self, db_session):
        """CL-007"""
        editor = _make_user("editor")
        _db.session.commit()

        with pytest.raises(svc.ContentLinkError) as exc:
            svc.remove_link(editor, 999_999)
        assert exc.value.status_code == 404


# ── List links ────────────────────────────────────────────────────────────────


class TestListLinks:
    def _setup(self, db_session):
        editor = _make_user("editor")
        hub = _make_post(editor)
        spoke1 = _make_post(editor)
        spoke2 = _make_post(editor)
        _db.session.commit()

        svc.add_link(editor, hub, spoke1, "related")   # hub → spoke1
        svc.add_link(editor, spoke2, hub, "derived_from")  # spoke2 → hub
        _db.session.commit()
        return hub, spoke1, spoke2

    def test_outgoing_only(self, db_session):
        """CL-008"""
        hub, spoke1, spoke2 = self._setup(db_session)
        links = svc.list_links_for_post(hub, workspace_id=None, direction="outgoing")
        assert all(lnk.from_post_id == hub.id for lnk in links)

    def test_incoming_only(self, db_session):
        """CL-009"""
        hub, spoke1, spoke2 = self._setup(db_session)
        links = svc.list_links_for_post(hub, workspace_id=None, direction="incoming")
        assert all(lnk.to_post_id == hub.id for lnk in links)

    def test_grouped_structure(self, db_session):
        """CL-010"""
        hub, spoke1, spoke2 = self._setup(db_session)
        grouped = svc.list_links_grouped(hub, workspace_id=None)

        assert "outgoing" in grouped
        assert "incoming" in grouped
        assert "related" in grouped["outgoing"]
        out_ids = [lnk.to_post_id for lnk in grouped["outgoing"]["related"]]
        assert spoke1.id in out_ids

        assert "derived_from" in grouped["incoming"]
        in_ids = [lnk.from_post_id for lnk in grouped["incoming"]["derived_from"]]
        assert spoke2.id in in_ids

    def test_get_link_or_none_missing(self, db_session):
        """CL-011"""
        result = svc.get_link_or_none(999_999)
        assert result is None


# ── Cascade delete ────────────────────────────────────────────────────────────


class TestCascadeDelete:
    def test_post_delete_cascades_to_links(self, db_session):
        """CL-012"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _db.session.commit()

        link = svc.add_link(editor, p1, p2, "implements")
        _db.session.commit()
        link_id = link.id

        # Delete from_post → link should be gone.
        _db.session.delete(p1)
        _db.session.commit()

        assert _db.session.get(ContentLink, link_id) is None
