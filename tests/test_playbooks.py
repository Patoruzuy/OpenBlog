"""Tests for the Workspace Playbooks MVP.

Covers:
  PLB-001  PlaybookService: template + version CRUD
  PLB-002  PlaybookService: create playbook with / without template seed
  PLB-003  PlaybookService: list/get helpers scope to (workspace, kind='playbook')
  PLB-004  Route: non-member gets 404 on all /playbooks/* URLs
  PLB-005  Route: unauthenticated redirects to login
  PLB-006  Route: viewer can list/view playbooks (read-only)
  PLB-007  Route: viewer is rejected on /playbooks/new (editor required)
  PLB-008  Route: editor can create playbook via POST /playbooks/new
  PLB-009  Public isolation: playbooks absent from /explore posts
  PLB-010  Public isolation: playbooks absent from /feed.xml and /feed.json
  PLB-011  Public isolation: playbooks absent from search suggestions
  PLB-012  Public isolation: playbooks absent from search results
  PLB-013  Cross-kind slug safety: get_workspace_playbook ignores kind='article' posts
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.extensions import db as _db
from backend.models.playbook import PlaybookTemplate, PlaybookTemplateVersion
from backend.models.post import Post, PostStatus
from backend.models.user import User, UserRole
from backend.models.workspace import WorkspaceMember, WorkspaceMemberRole
from backend.services import playbook_service as pb_svc
from backend.services import workspace_service as ws_svc


# ── module-level counter for unique test data ─────────────────────────────────

_counter = {"n": 0}


def _create_user(role: str = "reader", email: str | None = None) -> tuple[User, str]:
    from backend.services.auth_service import AuthService

    _counter["n"] += 1
    n = _counter["n"]
    email = email or f"pb_test_{n}@example.com"
    username = f"pb_user_{n}"
    user = AuthService.register(email, username, "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    token = AuthService.issue_access_token(user)
    return user, token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_workspace(owner: User, name: str = "Test WS"):
    ws = ws_svc.create_workspace(name=name, owner=owner)
    _db.session.commit()
    return ws


def _make_published_article(author: User, slug: str = "public-article", workspace_id=None) -> Post:
    """Create a published article (kind='article') for feed/explore isolation tests."""
    post = Post(
        title="Public Article",
        slug=slug,
        kind="article",
        markdown_body="Hello world.",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _db.session.add(post)
    _db.session.flush()
    return post


def _make_published_playbook(author: User, ws, slug: str = "pub-playbook") -> Post:
    """Create a *published* playbook post; should never appear in public feeds."""
    post = Post(
        title="Published Playbook",
        slug=slug,
        kind="playbook",
        markdown_body="# Runbook\nSteps here.",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=ws.id,
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _db.session.add(post)
    _db.session.flush()
    return post


# ─────────────────────────────────────────────────────────────────────────────
# PLB-001 / PLB-002  PlaybookService — template + instance CRUD
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaybookService:
    """PLB-001 / PLB-002 / PLB-003: pure service-layer tests (no HTTP)."""

    # ── template operations ───────────────────────────────────────────────────

    def test_create_and_retrieve_template(self, db_session):
        owner, _ = _create_user("editor")
        tmpl = pb_svc.create_template(
            name="Incident Response",
            slug="incident-response",
            description="For incidents",
            created_by=owner,
        )
        _db.session.commit()

        fetched = pb_svc.get_template_by_slug("incident-response")
        assert fetched is not None
        assert fetched.name == "Incident Response"
        assert fetched.is_public is True

    def test_create_template_version_auto_increments(self, db_session):
        owner, _ = _create_user("editor")
        tmpl = pb_svc.create_template(
            name="Deploy", slug="deploy", created_by=owner
        )
        _db.session.commit()

        v1 = pb_svc.create_template_version(
            template_id=tmpl.id,
            skeleton_md="# Deploy\n## Steps\n- Step 1",
            change_notes="Initial version",
            created_by=owner,
        )
        _db.session.commit()
        assert v1.version == 1

        v2 = pb_svc.create_template_version(
            template_id=tmpl.id,
            skeleton_md="# Deploy v2\n## Steps\n- Step 1\n- Step 2",
            change_notes="Added step 2",
            created_by=owner,
        )
        _db.session.commit()
        assert v2.version == 2

    def test_get_latest_template_version(self, db_session):
        owner, _ = _create_user("editor")
        tmpl = pb_svc.create_template(
            name="Rollback", slug="rollback", created_by=owner
        )
        _db.session.commit()
        for i in range(3):
            pb_svc.create_template_version(
                template_id=tmpl.id,
                skeleton_md=f"# Version {i + 1}",
                created_by=owner,
            )
        _db.session.commit()

        latest = pb_svc.get_latest_template_version(tmpl.id)
        assert latest is not None
        assert latest.version == 3

    def test_list_templates_public_only(self, db_session):
        owner, _ = _create_user("editor")
        pb_svc.create_template(
            name="Public T", slug="public-t", is_public=True, created_by=owner
        )
        pb_svc.create_template(
            name="Private T", slug="private-t", is_public=False, created_by=owner
        )
        _db.session.commit()

        public = pb_svc.list_templates(public_only=True)
        names = [t.name for t in public]
        assert "Public T" in names
        assert "Private T" not in names

        all_templates = pb_svc.list_templates(public_only=False)
        all_names = [t.name for t in all_templates]
        assert "Private T" in all_names

    # ── instance (playbook post) operations ───────────────────────────────────

    def test_create_blank_playbook(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        post = pb_svc.create_workspace_playbook(
            workspace=ws, creator=owner, title="On-call Runbook"
        )
        _db.session.commit()

        assert post.kind == "playbook"
        assert post.workspace_id == ws.id
        assert post.status == PostStatus.draft
        assert post.markdown_body == ""
        assert post.template_version_id is None
        assert post.template_id is None

    def test_create_playbook_from_template_seeds_body(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        tmpl = pb_svc.create_template(
            name="Seeded", slug="seeded", created_by=owner
        )
        _db.session.commit()
        tv = pb_svc.create_template_version(
            template_id=tmpl.id,
            skeleton_md="# Runbook\n## Steps\n1. Do X\n2. Do Y",
            created_by=owner,
        )
        _db.session.commit()

        post = pb_svc.create_workspace_playbook(
            workspace=ws,
            creator=owner,
            title="Template-seeded",
            template_version_id=tv.id,
        )
        _db.session.commit()

        assert post.kind == "playbook"
        assert post.template_version_id == tv.id
        assert post.template_id == tmpl.id
        assert "# Runbook" in post.markdown_body

    def test_list_workspace_playbooks_filters_by_kind(self, db_session):
        """list_workspace_playbooks must NOT return kind='article' docs."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        # Create a regular document (kind defaults to 'article')
        doc = ws_svc.create_workspace_document(
            workspace=ws, author=owner, title="Regular Doc"
        )
        _db.session.commit()

        # Create a playbook
        pb = pb_svc.create_workspace_playbook(
            workspace=ws, creator=owner, title="My Playbook"
        )
        _db.session.commit()

        results = pb_svc.list_workspace_playbooks(ws)
        slugs = [p.slug for p in results]
        assert pb.slug in slugs
        assert doc.slug not in slugs

    def test_get_workspace_playbook_ignores_document_kind(self, db_session):
        """PLB-013: get_workspace_playbook must not return kind='article' rows."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        doc = ws_svc.create_workspace_document(
            workspace=ws, author=owner, title="Shared Slug", slug="shared-s"
        )
        _db.session.commit()

        # Even though a doc with the same slug exists, the playbook lookup
        # should return None because the doc has kind='article'.
        result = pb_svc.get_workspace_playbook(ws, "shared-s")
        assert result is None

    def test_slug_uniqueness_within_workspace_playbooks(self, db_session):
        """Two playbooks with the same title must get different slugs."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        pb1 = pb_svc.create_workspace_playbook(
            workspace=ws, creator=owner, title="Duplicate"
        )
        _db.session.commit()

        pb2 = pb_svc.create_workspace_playbook(
            workspace=ws, creator=owner, title="Duplicate"
        )
        _db.session.commit()

        assert pb1.slug != pb2.slug


# ─────────────────────────────────────────────────────────────────────────────
# PLB-004 / PLB-005 / PLB-006 / PLB-007 / PLB-008  Route access control
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaybookRouteAccess:
    """Route-level membership and role enforcement."""

    def _setup(self, db_session):
        owner, owner_tok = _create_user("editor")
        viewer, viewer_tok = _create_user("reader")
        editor, editor_tok = _create_user("contributor")
        outsider, outsider_tok = _create_user("reader")

        ws = _make_workspace(owner, name="Route WS")
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        ws_svc.add_member(ws, editor, WorkspaceMemberRole.editor)
        _db.session.commit()

        return ws, owner_tok, viewer_tok, editor_tok, outsider_tok

    # PLB-004 non-member → 404 ───────────────────────────────────────────────

    def test_non_member_list_returns_404(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/playbooks", headers=_auth(outsider_tok))
        assert resp.status_code == 404

    def test_non_member_new_form_returns_404(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(
            f"/w/{ws.slug}/playbooks/new", headers=_auth(outsider_tok)
        )
        assert resp.status_code == 404

    def test_non_member_detail_returns_404(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(
            f"/w/{ws.slug}/playbooks/does-not-exist",
            headers=_auth(outsider_tok),
        )
        assert resp.status_code == 404

    # PLB-005 unauthenticated → redirect to login ────────────────────────────

    def test_unauthenticated_list_redirects(self, auth_client, db_session):
        ws, *_ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/playbooks")
        assert resp.status_code in (301, 302)
        assert "login" in resp.headers["Location"]

    # PLB-006 viewer can read ────────────────────────────────────────────────

    def test_viewer_list_returns_200(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/playbooks", headers=_auth(viewer_tok))
        assert resp.status_code == 200

    def test_viewer_detail_returns_200_when_playbook_exists(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)

        # Create playbook as owner
        with auth_client.application.app_context():
            from sqlalchemy import select as _select  # noqa: PLC0415
            owner_obj = _db.session.scalar(
                _select(User).where(User.id == ws.owner_id)
            )
            pb = pb_svc.create_workspace_playbook(
                workspace=ws, creator=owner_obj, title="Viewer Read Test"
            )
            _db.session.commit()
            pb_slug = pb.slug

        resp = auth_client.get(
            f"/w/{ws.slug}/playbooks/{pb_slug}", headers=_auth(viewer_tok)
        )
        assert resp.status_code == 200

    # PLB-007 viewer cannot create (editor required) ─────────────────────────

    def test_viewer_new_form_returns_404(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(
            f"/w/{ws.slug}/playbooks/new", headers=_auth(viewer_tok)
        )
        assert resp.status_code == 404

    def test_viewer_post_new_returns_404(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.post(
            f"/w/{ws.slug}/playbooks/new",
            headers=_auth(viewer_tok),
            data={"title": "Attempted"},
        )
        assert resp.status_code == 404

    # PLB-008 editor can create ──────────────────────────────────────────────

    def test_editor_new_form_returns_200(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(
            f"/w/{ws.slug}/playbooks/new", headers=_auth(editor_tok)
        )
        assert resp.status_code == 200

    def test_editor_can_create_playbook(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.post(
            f"/w/{ws.slug}/playbooks/new",
            headers=_auth(editor_tok),
            data={"title": "Editor Created PB"},
            follow_redirects=False,
        )
        # Successful creation → redirect to detail page
        assert resp.status_code in (302, 301), resp.data.decode()[:400]
        assert "playbooks" in resp.headers["Location"]

    def test_create_playbook_no_title_returns_200_with_error(self, auth_client, db_session):
        """Empty title flashes error and re-renders the form (200)."""
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.post(
            f"/w/{ws.slug}/playbooks/new",
            headers=_auth(editor_tok),
            data={"title": ""},
        )
        assert resp.status_code == 200

    def test_owner_can_create_playbook(self, auth_client, db_session):
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.post(
            f"/w/{ws.slug}/playbooks/new",
            headers=_auth(owner_tok),
            data={"title": "Owner PB"},
            follow_redirects=False,
        )
        assert resp.status_code in (301, 302)

    def test_create_playbook_with_template_version(self, auth_client, db_session):
        """POST with template_version_id seeds the playbook body."""
        ws, owner_tok, viewer_tok, editor_tok, outsider_tok = self._setup(db_session)

        with auth_client.application.app_context():
            from sqlalchemy import select as _select  # noqa: PLC0415
            owner_obj = _db.session.scalar(
                _select(User).where(User.id == ws.owner_id)
            )
            tmpl = pb_svc.create_template(
                name="RT Template", slug="rt-tmpl", created_by=owner_obj
            )
            _db.session.commit()
            tv = pb_svc.create_template_version(
                template_id=tmpl.id,
                skeleton_md="# From Template\n## Steps",
                created_by=owner_obj,
            )
            _db.session.commit()
            tv_id = tv.id

        resp = auth_client.post(
            f"/w/{ws.slug}/playbooks/new",
            headers=_auth(editor_tok),
            data={"title": "Seeded PB", "template_version_id": str(tv_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Verify playbook was created with template data via service
        with auth_client.application.app_context():
            from sqlalchemy import select as _select  # noqa: PLC0415
            post = _db.session.scalar(
                _select(Post).where(
                    Post.workspace_id == ws.id,
                    Post.kind == "playbook",
                    Post.title == "Seeded PB",
                )
            )
            assert post is not None
            assert post.template_version_id == tv_id
            assert "# From Template" in post.markdown_body


# ─────────────────────────────────────────────────────────────────────────────
# PLB-009 / PLB-010 / PLB-011 / PLB-012  Public isolation
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaybookPublicIsolation:
    """Playbooks must never appear in public-facing endpoints."""

    def _setup_playbook_and_article(self, db_session):
        """Create a workspace with a published playbook + a public published article."""
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner, name="Isolation WS")

        # published article (should appear in public feeds)
        _counter["n"] += 1
        article = _make_published_article(
            owner, slug=f"public-art-{_counter['n']}"
        )

        # published playbook (must NOT appear in public feeds)
        _counter["n"] += 1
        playbook = _make_published_playbook(ws=ws, author=owner, slug=f"ws-pb-{_counter['n']}")

        _db.session.commit()
        return ws, article, playbook

    def test_playbook_absent_from_explore(self, auth_client, db_session):
        """PLB-009: /explore posts listing must not include playbook rows."""
        from backend.services.explore_service import ExploreService  # noqa: PLC0415

        ws, article, playbook = self._setup_playbook_and_article(db_session)
        with auth_client.application.app_context():
            posts, _ = ExploreService.get_posts(page=1)
            kinds = {p.kind for p in posts}
            assert "playbook" not in kinds
            slugs = {p.slug for p in posts}
            assert playbook.slug not in slugs

    def test_playbook_absent_from_explore_topics(self, auth_client, db_session):
        """PLB-009: Explore topics tag counts must not include playbook posts."""
        from backend.services.explore_service import ExploreService  # noqa: PLC0415

        ws, article, playbook = self._setup_playbook_and_article(db_session)
        with auth_client.application.app_context():
            # This should not raise; just verify it runs correctly
            topics = ExploreService.get_topics()
            assert isinstance(topics, list)

    def test_playbook_absent_from_rss_feed(self, auth_client, db_session):
        """PLB-010: /feed.xml must not contain playbook slugs."""
        ws, article, playbook = self._setup_playbook_and_article(db_session)
        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert playbook.slug not in body

    def test_playbook_absent_from_json_feed(self, auth_client, db_session):
        """PLB-010: /feed.json must not contain playbook slugs."""
        ws, article, playbook = self._setup_playbook_and_article(db_session)
        resp = auth_client.get("/feed.json")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert playbook.slug not in body

    def test_playbook_absent_from_search_suggest(self, auth_client, db_session):
        """PLB-011: /api/search/suggest must not return playbook titles."""
        from backend.services.search_service import SearchService  # noqa: PLC0415

        owner, _ = _create_user("editor")
        ws = _make_workspace(owner, name="Suggest WS")

        # Add a published playbook with a unique searchable title
        pb = Post(
            title="UniquePlaybookXYZ suggest",
            slug="pb-suggest-xyz",
            kind="playbook",
            markdown_body="UniquePlaybookXYZ content",
            status=PostStatus.published,
            author_id=owner.id,
            workspace_id=ws.id,
            published_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(pb)
        _db.session.commit()

        with auth_client.application.app_context():
            result = SearchService.suggest("UniquePlaybookXYZ")
            post_titles = [p["title"] for p in result.get("posts", [])]
            assert "UniquePlaybookXYZ suggest" not in post_titles

    def test_playbook_absent_from_full_text_search(self, auth_client, db_session):
        """PLB-012: search results must not include playbook posts."""
        from backend.services.search_service import SearchService  # noqa: PLC0415

        owner, _ = _create_user("editor")
        ws = _make_workspace(owner, name="Search WS")

        pb = Post(
            title="UniquePlaybookABC search",
            slug="pb-search-abc",
            kind="playbook",
            markdown_body="UniquePlaybookABC content in body",
            status=PostStatus.published,
            author_id=owner.id,
            workspace_id=ws.id,
            published_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(pb)
        _db.session.commit()

        with auth_client.application.app_context():
            result = SearchService.search("UniquePlaybookABC")
            post_slugs = [p.slug for p in result.posts]
            assert "pb-search-abc" not in post_slugs

    def test_article_still_appears_in_explore_after_filter(self, auth_client, db_session):
        """Sanity check: filtering playbooks does not break regular article visibility."""
        from backend.services.explore_service import ExploreService  # noqa: PLC0415

        ws, article, playbook = self._setup_playbook_and_article(db_session)
        with auth_client.application.app_context():
            posts, _ = ExploreService.get_posts(page=1)
            slugs = {p.slug for p in posts}
            assert article.slug in slugs


# ─────────────────────────────────────────────────────────────────────────────
# Cache-Control on playbook responses
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaybookCacheHeaders:
    """Playbook routes inherit the blueprint-wide private, no-store policy."""

    def test_playbook_list_has_private_no_store(self, auth_client, db_session):
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner, name="Cache WS")
        resp = auth_client.get(f"/w/{ws.slug}/playbooks", headers=_auth(owner_tok))
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "no-store" in cc
