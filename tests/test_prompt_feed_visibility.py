"""Tests for Prompt Library visibility in feeds and sitemap.

Coverage
--------
  PRFV-001  Published public prompt appears in /feed.xml (Atom).
  PRFV-002  Published public prompt appears in /feed.json (JSON Feed).
  PRFV-003  Workspace prompt NOT in /feed.xml.
  PRFV-004  Workspace prompt NOT in /feed.json.
  PRFV-005  Published public prompt appears in /sitemap.xml.
  PRFV-006  Workspace prompt NOT in /sitemap.xml.
  PRFV-007  Draft public prompt NOT in feeds.
  PRFV-008  Feed URL for prompt links to /prompts/<slug>.
  PRFV-009  Sitemap URL for prompt links to /prompts/<slug>.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.post import PostStatus
from backend.services import prompt_service as svc
from backend.services import workspace_service as ws_svc

_ctr = itertools.count(300)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.services.auth_service import AuthService

    n = _n()
    return AuthService.register(f"fv{n}@example.com", f"fvuser{n}", "StrongPass123!!")


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"FV WS {n}", owner=owner)
    _db.session.commit()
    return ws


# ── Atom feed (/feed.xml) ─────────────────────────────────────────────────────


class TestAtomFeed:
    def test_published_prompt_in_atom_feed(self, db_session, auth_client):
        """PRFV-001"""
        user = _make_user()
        post = svc.create_prompt(
            title="Feed Atom Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        xml = resp.data.decode()
        assert post.slug in xml

    def test_workspace_prompt_absent_from_atom_feed(self, db_session, auth_client):
        """PRFV-003"""
        owner = _make_user()
        ws = _make_workspace(owner)
        post = svc.create_prompt(
            title="WS Atom Hidden Prompt",
            markdown_body="body",
            author=owner,
            workspace_id=ws.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        xml = resp.data.decode()
        assert post.slug not in xml
        assert post.title not in xml

    def test_draft_prompt_absent_from_atom_feed(self, db_session, auth_client):
        """PRFV-007"""
        user = _make_user()
        post = svc.create_prompt(
            title="Draft Atom Hidden Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.draft,
        )
        _db.session.commit()

        resp = auth_client.get("/feed.xml")
        xml = resp.data.decode()
        assert post.slug not in xml

    def test_atom_feed_url_points_to_prompts_route(self, db_session, auth_client):
        """PRFV-008"""
        user = _make_user()
        post = svc.create_prompt(
            title="URL Check Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/feed.xml")
        xml = resp.data.decode()
        assert f"/prompts/{post.slug}" in xml
        # Must NOT link via the article route.
        assert f"/posts/{post.slug}" not in xml


# ── JSON Feed (/feed.json) ────────────────────────────────────────────────────


class TestJsonFeed:
    def test_published_prompt_in_json_feed(self, db_session, auth_client):
        """PRFV-002"""
        user = _make_user()
        post = svc.create_prompt(
            title="JSON Feed Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/feed.json")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert post.slug in body

    def test_workspace_prompt_absent_from_json_feed(self, db_session, auth_client):
        """PRFV-004"""
        owner = _make_user()
        ws = _make_workspace(owner)
        post = svc.create_prompt(
            title="WS JSON Hidden Prompt",
            markdown_body="body",
            author=owner,
            workspace_id=ws.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/feed.json")
        body = resp.data.decode()
        assert post.slug not in body
        assert post.title not in body

    def test_json_feed_url_points_to_prompts_route(self, db_session, auth_client):
        """PRFV-008 (JSON variant)"""
        user = _make_user()
        post = svc.create_prompt(
            title="JSON URL Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/feed.json")
        body = resp.data.decode()
        assert f"/prompts/{post.slug}" in body
        assert f"/posts/{post.slug}" not in body


# ── Sitemap (/sitemap.xml) ────────────────────────────────────────────────────


class TestSitemap:
    def test_published_prompt_in_sitemap(self, db_session, auth_client):
        """PRFV-005"""
        user = _make_user()
        post = svc.create_prompt(
            title="Sitemap Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/sitemap.xml")
        assert resp.status_code == 200
        xml = resp.data.decode()
        assert post.slug in xml

    def test_workspace_prompt_absent_from_sitemap(self, db_session, auth_client):
        """PRFV-006"""
        owner = _make_user()
        ws = _make_workspace(owner)
        post = svc.create_prompt(
            title="WS Sitemap Hidden Prompt",
            markdown_body="body",
            author=owner,
            workspace_id=ws.id,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/sitemap.xml")
        xml = resp.data.decode()
        assert post.slug not in xml

    def test_sitemap_prompt_url_correct(self, db_session, auth_client):
        """PRFV-009"""
        user = _make_user()
        post = svc.create_prompt(
            title="Sitemap URL Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        resp = auth_client.get("/sitemap.xml")
        xml = resp.data.decode()
        assert f"/prompts/{post.slug}" in xml
        assert f"/posts/{post.slug}" not in xml
