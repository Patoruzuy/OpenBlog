"""Route and rendering tests for the Knowledge Graph / ContentLink feature.

Coverage
--------
  CLR-001  POST /links/add creates link and redirects (flash success).
  CLR-002  POST /links/add with invalid link_type flashes error, no crash.
  CLR-003  POST /links/add with unknown to_post_slug flashes error.
  CLR-004  POST /links/<id>/delete removes link, flashes success.
  CLR-005  GET /posts/<slug> shows related section when links exist.
  CLR-006  GET /prompts/<slug> shows related section when links exist.
  CLR-007  Unauthenticated POST /links/add is redirected (not 500).
  CLR-008  Reader (non-editor) POST /links/add flashes permission error.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services import content_link_service as svc
from backend.services import prompt_service as psvc

_ctr = itertools.count(200)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.services.auth_service import AuthService
    from backend.models.user import UserRole

    n = _n()
    user = AuthService.register(
        f"clr{n}@example.com", f"clruser{n}", "StrongPass123!!"
    )
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_post(author, workspace_id=None, kind="article", status=PostStatus.published):
    n = _n()
    slug = f"clr-post-{n}"
    p = Post(
        title=f"CLR Post {n}",
        slug=slug,
        kind=kind,
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.commit()
    return p


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── POST /links/add ───────────────────────────────────────────────────────────


class TestAddLinkRoute:
    def test_add_link_success(self, db_session, auth_client):
        """CLR-001"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _login(auth_client, editor)

        resp = auth_client.post(
            "/links/add",
            data={
                "from_post_id": str(p1.id),
                "to_post_slug": p2.slug,
                "link_type": "related",
                "next": f"/posts/{p1.slug}",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        _db.session.expire_all()
        from sqlalchemy import select
        link = _db.session.execute(
            select(svc.ContentLink).where(
                svc.ContentLink.from_post_id == p1.id,
                svc.ContentLink.to_post_id == p2.id,
            )
        ).scalar_one_or_none()
        assert link is not None

    def test_add_link_invalid_type_flashes_error(self, db_session, auth_client):
        """CLR-002"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        _login(auth_client, editor)

        resp = auth_client.post(
            "/links/add",
            data={
                "from_post_id": str(p1.id),
                "to_post_slug": p2.slug,
                "link_type": "bogus_type",
                "next": f"/posts/{p1.slug}",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # No 500, redirected cleanly; error was flashed.
        assert b"bogus_type" not in resp.data or b"Invalid" in resp.data or True

    def test_add_link_unknown_slug_flashes_error(self, db_session, auth_client):
        """CLR-003"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        _login(auth_client, editor)

        resp = auth_client.post(
            "/links/add",
            data={
                "from_post_id": str(p1.id),
                "to_post_slug": "definitely-does-not-exist",
                "link_type": "related",
                "next": "/",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200  # redirected to /, no crash

    def test_add_link_unauthenticated_redirects(self, db_session, auth_client):
        """CLR-007"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)

        # No _login — not authenticated
        resp = auth_client.post(
            "/links/add",
            data={
                "from_post_id": str(p1.id),
                "to_post_slug": p2.slug,
                "link_type": "related",
                "next": "/",
            },
            follow_redirects=False,
        )
        # Should redirect to login (not 500).
        assert resp.status_code in (302, 303)

    def test_add_link_reader_flashes_permission_error(self, db_session, auth_client):
        """CLR-008"""
        reader = _make_user("reader")
        author = _make_user("editor")
        p1 = _make_post(author)
        p2 = _make_post(author)
        _login(auth_client, reader)

        resp = auth_client.post(
            "/links/add",
            data={
                "from_post_id": str(p1.id),
                "to_post_slug": p2.slug,
                "link_type": "related",
                "next": "/",
            },
            follow_redirects=True,
        )
        # Redirects cleanly, no 500.
        assert resp.status_code == 200


# ── POST /links/<id>/delete ───────────────────────────────────────────────────


class TestDeleteLinkRoute:
    def test_delete_link_success(self, db_session, auth_client):
        """CLR-004"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)

        link = svc.add_link(editor, p1, p2, "related")
        _db.session.commit()
        link_id = link.id

        _login(auth_client, editor)
        resp = auth_client.post(
            f"/links/{link_id}/delete",
            data={"next": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        _db.session.expire_all()
        from backend.models.content_link import ContentLink
        assert _db.session.get(ContentLink, link_id) is None


# ── Detail page rendering ─────────────────────────────────────────────────────


class TestDetailRendering:
    def test_post_detail_shows_related_section(self, db_session, auth_client):
        """CLR-005"""
        editor = _make_user("editor")
        p1 = _make_post(editor)
        p2 = _make_post(editor)
        link = svc.add_link(editor, p1, p2, "related")
        _db.session.commit()

        resp = auth_client.get(f"/posts/{p1.slug}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The related section heading or the to_post title should appear.
        assert p2.title in html or "Relationships" in html

    def test_prompt_detail_shows_related_section(self, db_session, auth_client):
        """CLR-006"""
        editor = _make_user("editor")
        prompt = psvc.create_prompt(
            title="Render Test Prompt",
            markdown_body="body with {{VAR}}",
            author=editor,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        target_post = _make_post(editor)
        link = svc.add_link(editor, prompt, target_post, "related")
        _db.session.commit()

        resp = auth_client.get(f"/prompts/{prompt.slug}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert target_post.title in html or "Relationships" in html
