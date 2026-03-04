"""Security invariant tests for the Workspace Layer (Phase 1).

Covers:
  INV-001  public content = workspace_id IS NULL AND published AND published_at IS NOT NULL
  INV-002  non-member gets 404 (never 403/200) on all /w/ routes
  INV-003  viewer can view, cannot create/edit documents
  INV-004  editor can create and edit documents
  INV-005  contributor can suggest revisions but not accept
  INV-006  owner may clone to public; viewer/contributor may not
  INV-007  clone creates public draft (workspace_id=NULL, status=draft)
  INV-008  /feed.xml + /feed.json contain only public published posts
  INV-009  /sitemap.xml excludes workspace posts and non-published content
  INV-010  Cache-Control: private, no-store on all /w/ responses
  INV-011  Cache-Control: public on /feed.xml and /sitemap.xml
  INV-012  ETag of /feed.xml does NOT change when workspace-only content changes
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.user import User, UserRole
from backend.models.workspace import WorkspaceMemberRole
from backend.security.permissions import PermissionService
from backend.services import workspace_service as ws_svc

# ── helpers ───────────────────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_published_post(
    author: User,
    *,
    workspace_id: int | None = None,
    title: str = "Public Post",
    slug: str = "public-post",
) -> Post:
    """Create and flush a published post with workspace_id scoped appropriately."""
    post = Post(
        title=title,
        slug=slug,
        markdown_body="Hello **world**.",
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


# ── PermissionService unit tests ──────────────────────────────────────────────


class TestPermissionServicePublicPost:
    """INV-001 — public-layer visibility rules (object-level)."""

    def test_anonymous_can_view_published_public_post(self, db_session):
        user, _ = _create_user("reader")
        post = _make_published_post(user, slug="pub-1")
        assert PermissionService.can_view_post(None, post) is True

    def test_anonymous_cannot_view_public_draft(self, db_session):
        user, _ = _create_user("contributor")
        draft = Post(
            title="Draft",
            slug="draft-1",
            markdown_body="",
            status=PostStatus.draft,
            author_id=user.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(draft)
        _db.session.flush()
        assert PermissionService.can_view_post(None, draft) is False

    def test_author_can_view_own_draft(self, db_session):
        user, _ = _create_user("contributor")
        draft = Post(
            title="Draft",
            slug="draft-2",
            markdown_body="",
            status=PostStatus.draft,
            author_id=user.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(draft)
        _db.session.flush()
        assert PermissionService.can_view_post(user, draft) is True

    def test_admin_can_view_any_draft(self, db_session):
        author, _ = _create_user("contributor")
        admin, _ = _create_user("admin", email="admin2@example.com")
        draft = Post(
            title="Draft",
            slug="draft-3",
            markdown_body="",
            status=PostStatus.draft,
            author_id=author.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(draft)
        _db.session.flush()
        assert PermissionService.can_view_post(admin, draft) is True

    def test_other_user_cannot_view_others_draft(self, db_session):
        author, _ = _create_user("contributor")
        other, _ = _create_user("contributor", email="other@example.com")
        draft = Post(
            title="Draft",
            slug="draft-4",
            markdown_body="",
            status=PostStatus.draft,
            author_id=author.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(draft)
        _db.session.flush()
        assert PermissionService.can_view_post(other, draft) is False


class TestPermissionServiceWorkspacePost:
    """Workspace-layer visibility rules."""

    def _setup_workspace(self, owner_email="owner@example.com"):
        owner, tok = _create_user("editor", email=owner_email)
        ws = ws_svc.create_workspace(name="Test WS", owner=owner)
        _db.session.commit()
        return ws, owner, tok

    def test_member_can_view_workspace_post(self, db_session):
        ws, owner, _ = self._setup_workspace()
        post = Post(
            title="WS Doc",
            slug="ws-doc-1",
            markdown_body="",
            status=PostStatus.draft,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.flush()
        post.workspace = ws  # set eager relationship for permission check
        assert PermissionService.can_view_post(owner, post) is True

    def test_non_member_cannot_view_workspace_post(self, db_session):
        ws, owner, _ = self._setup_workspace()
        outsider, _ = _create_user("contributor", email="out@example.com")
        post = Post(
            title="WS Doc",
            slug="ws-doc-2",
            markdown_body="",
            status=PostStatus.published,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.flush()
        post.workspace = ws
        assert PermissionService.can_view_post(outsider, post) is False

    def test_admin_can_view_workspace_post_without_membership(self, db_session):
        ws, owner, _ = self._setup_workspace()
        admin, _ = _create_user("admin", email="admin3@example.com")
        post = Post(
            title="WS Doc",
            slug="ws-doc-3",
            markdown_body="",
            status=PostStatus.draft,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.flush()
        post.workspace = ws
        assert PermissionService.can_view_post(admin, post) is True

    def test_viewer_cannot_edit_workspace_post(self, db_session):
        ws, owner, _ = self._setup_workspace()
        viewer, _ = _create_user("reader", email="viewer@example.com")
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()
        post = Post(
            title="WS Doc",
            slug="ws-doc-4",
            markdown_body="",
            status=PostStatus.draft,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.flush()
        post.workspace = ws
        assert PermissionService.can_edit_post(viewer, post) is False

    def test_editor_can_edit_workspace_post(self, db_session):
        ws, owner, _ = self._setup_workspace()
        editor, _ = _create_user("contributor", email="editor2@example.com")
        ws_svc.add_member(ws, editor, WorkspaceMemberRole.editor)
        _db.session.commit()
        post = Post(
            title="WS Doc",
            slug="ws-doc-5",
            markdown_body="",
            status=PostStatus.draft,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.flush()
        post.workspace = ws
        assert PermissionService.can_edit_post(editor, post) is True

    def test_contributor_can_suggest_but_not_accept(self, db_session):
        ws, owner, _ = self._setup_workspace()
        contrib, _ = _create_user("contributor", email="contrib@example.com")
        ws_svc.add_member(ws, contrib, WorkspaceMemberRole.contributor)
        _db.session.commit()
        post = Post(
            title="WS Doc",
            slug="ws-doc-6",
            markdown_body="",
            status=PostStatus.draft,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.flush()
        post.workspace = ws
        assert PermissionService.can_suggest_revision(contrib, post) is True
        assert PermissionService.can_accept_revision(contrib, post) is False

    def test_clone_requires_editor_level(self, db_session):
        ws, owner, _ = self._setup_workspace()
        viewer, _ = _create_user("reader", email="viewer2@example.com")
        contrib, _ = _create_user("contributor", email="contrib2@example.com")
        editor, _ = _create_user("contributor", email="editor3@example.com")
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        ws_svc.add_member(ws, contrib, WorkspaceMemberRole.contributor)
        ws_svc.add_member(ws, editor, WorkspaceMemberRole.editor)
        _db.session.commit()
        post = Post(
            title="WS Doc",
            slug="ws-doc-7",
            markdown_body="",
            status=PostStatus.draft,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.flush()
        post.workspace = ws
        assert PermissionService.can_clone_to_public(viewer, post) is False
        assert PermissionService.can_clone_to_public(contrib, post) is False
        assert PermissionService.can_clone_to_public(editor, post) is True
        assert PermissionService.can_clone_to_public(owner, post) is True

    def test_clone_not_applicable_to_public_post(self, db_session):
        user, _ = _create_user("editor", email="ed@example.com")
        post = _make_published_post(user, slug="pub-clone-test")
        assert PermissionService.can_clone_to_public(user, post) is False


# ── Workspace route access control ────────────────────────────────────────────


class TestWorkspaceRouteAccess:
    """INV-002: non-member gets 404; INV-003/004: role enforcement."""

    def _setup(self, db_session):
        owner, owner_tok = _create_user("editor", email="owner_r@example.com")
        viewer, viewer_tok = _create_user("reader", email="viewer_r@example.com")
        outsider, outsider_tok = _create_user("reader", email="outsider_r@example.com")

        ws = ws_svc.create_workspace(name="Route Test WS", owner=owner)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        doc = ws_svc.create_workspace_document(
            workspace=ws,
            author=owner,
            title="Route Doc",
            markdown_body="body text",
        )
        _db.session.commit()
        return ws, doc, owner_tok, viewer_tok, outsider_tok

    def test_non_member_dashboard_returns_404(self, auth_client, db_session):
        ws, doc, owner_tok, viewer_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}", headers=_auth(outsider_tok))
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"

    def test_non_member_document_returns_404(self, auth_client, db_session):
        ws, doc, owner_tok, viewer_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(
            f"/w/{ws.slug}/docs/{doc.slug}", headers=_auth(outsider_tok)
        )
        assert resp.status_code == 404

    def test_unauthenticated_dashboard_redirects_to_login(
        self, auth_client, db_session
    ):
        ws, *_ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}")
        # require_auth redirects to login for unauthenticated users.
        assert resp.status_code in (302, 301)
        assert "login" in resp.headers["Location"]

    def test_viewer_dashboard_returns_200(self, auth_client, db_session):
        ws, doc, owner_tok, viewer_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}", headers=_auth(viewer_tok))
        assert resp.status_code == 200

    def test_viewer_document_returns_200(self, auth_client, db_session):
        ws, doc, owner_tok, viewer_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(
            f"/w/{ws.slug}/docs/{doc.slug}", headers=_auth(viewer_tok)
        )
        assert resp.status_code == 200

    def test_viewer_cannot_access_new_doc_form(self, auth_client, db_session):
        """Viewer (< editor) is rejected with 404 on GET /docs/new."""
        ws, doc, owner_tok, viewer_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/docs/new", headers=_auth(viewer_tok))
        # get_workspace_for_user(required_role=editor) → abort(404) for viewer.
        assert resp.status_code == 404

    def test_owner_can_access_new_doc_form(self, auth_client, db_session):
        ws, doc, owner_tok, viewer_tok, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/docs/new", headers=_auth(owner_tok))
        assert resp.status_code == 200

    def test_nonexistent_workspace_returns_404(self, auth_client, db_session):
        user, tok = _create_user("editor", email="nobody@example.com")
        resp = auth_client.get("/w/no-such-workspace", headers=_auth(tok))
        assert resp.status_code == 404


# ── Clone to public ───────────────────────────────────────────────────────────


class TestCloneToPublic:
    """INV-006/007: clone creates workspace_id=NULL draft; roles enforced."""

    def _setup(self, db_session):
        owner, owner_tok = _create_user("editor", email="owner_c@example.com")
        viewer, viewer_tok = _create_user("reader", email="viewer_c@example.com")
        contrib, contrib_tok = _create_user(
            "contributor", email="contrib_c@example.com"
        )
        editor, editor_tok = _create_user("contributor", email="editor_c@example.com")

        ws = ws_svc.create_workspace(name="Clone WS", owner=owner)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        ws_svc.add_member(ws, contrib, WorkspaceMemberRole.contributor)
        ws_svc.add_member(ws, editor, WorkspaceMemberRole.editor)
        _db.session.commit()

        doc = ws_svc.create_workspace_document(
            workspace=ws,
            author=owner,
            title="My Secret Doc",
            markdown_body="content here",
        )
        _db.session.commit()
        return ws, doc, owner, owner_tok, viewer_tok, contrib_tok, editor_tok

    def test_service_clone_creates_public_draft(self, db_session):
        ws, doc, owner, *_ = self._setup(db_session)
        clone = ws_svc.clone_to_public(doc, owner)
        _db.session.commit()

        assert clone.id is not None
        assert clone.workspace_id is None, "Clone must be on public layer"
        assert clone.status == PostStatus.draft, "Clone must start as draft"
        assert clone.author_id == owner.id
        assert clone.title == doc.title

    def test_service_clone_does_not_modify_original(self, db_session):
        ws, doc, owner, *_ = self._setup(db_session)
        original_ws_id = doc.workspace_id
        ws_svc.clone_to_public(doc, owner)
        _db.session.commit()

        # Reload the original.
        _db.session.expire(doc)
        assert doc.workspace_id == original_ws_id, "Original must remain in workspace"

    def test_service_clone_rejects_already_public_post(self, db_session):
        author, _ = _create_user("editor", email="clone_pub@example.com")
        post = _make_published_post(author, slug="already-public")
        with pytest.raises(ValueError, match="already on the public layer"):
            ws_svc.clone_to_public(post, author)

    def test_owner_clone_route_creates_public_draft(self, auth_client, db_session):
        ws, doc, owner, owner_tok, *_ = self._setup(db_session)
        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{doc.slug}/clone-to-public",
            headers=_auth(owner_tok),
            follow_redirects=False,
        )
        # Expect redirect to the new post's edit page or the document page.
        assert resp.status_code in (301, 302)

        # Verify a public draft was actually created.
        from sqlalchemy import select

        clone = _db.session.scalar(
            select(Post).where(
                Post.workspace_id.is_(None),
                Post.title == doc.title,
            )
        )
        assert clone is not None
        assert clone.status == PostStatus.draft

    def test_viewer_clone_route_returns_403(self, auth_client, db_session):
        ws, doc, owner, owner_tok, viewer_tok, *_ = self._setup(db_session)
        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{doc.slug}/clone-to-public",
            headers=_auth(viewer_tok),
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_contributor_clone_route_returns_403(self, auth_client, db_session):
        ws, doc, owner, owner_tok, viewer_tok, contrib_tok, editor_tok = self._setup(
            db_session
        )
        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{doc.slug}/clone-to-public",
            headers=_auth(contrib_tok),
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_non_member_clone_route_returns_404(self, auth_client, db_session):
        ws, doc, owner, owner_tok, *_ = self._setup(db_session)
        outsider, outsider_tok = _create_user("editor", email="outsider_c@example.com")
        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{doc.slug}/clone-to-public",
            headers=_auth(outsider_tok),
            follow_redirects=False,
        )
        assert resp.status_code == 404


# ── Feed isolation ────────────────────────────────────────────────────────────


class TestFeedIsolation:
    """INV-008: public feeds include ONLY public published posts."""

    def _seed(self, db_session):
        author, _ = _create_user("editor", email="feed_author@example.com")
        ws_owner, _ = _create_user("editor", email="ws_owner@example.com")
        ws = ws_svc.create_workspace(name="Feed WS", owner=ws_owner)
        _db.session.commit()

        # Public published post — should appear in feed.
        pub = _make_published_post(author, slug="feed-pub", title="Public Published")

        # Public draft — must NOT appear in feed.
        draft = Post(
            title="Draft Post",
            slug="feed-draft",
            markdown_body="draft",
            status=PostStatus.draft,
            author_id=author.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(draft)

        # Workspace post (even if "published") — must NOT appear in feed.
        ws_post = Post(
            title="Workspace Secret",
            slug="ws-secret",
            markdown_body="secret",
            status=PostStatus.published,
            author_id=ws_owner.id,
            workspace_id=ws.id,
            published_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(ws_post)
        _db.session.commit()
        return pub, draft, ws_post

    def test_rss_feed_excludes_workspace_posts(self, auth_client, db_session):
        pub, _draft, ws_post = self._seed(db_session)
        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert pub.title in body, "Public published post must appear in RSS feed"
        assert ws_post.title not in body, "Workspace post must NOT appear in RSS feed"

    def test_rss_feed_excludes_drafts(self, auth_client, db_session):
        _pub, draft, _ws_post = self._seed(db_session)
        resp = auth_client.get("/feed.xml")
        body = resp.data.decode()
        assert draft.title not in body, "Draft must NOT appear in RSS feed"

    def test_json_feed_excludes_workspace_posts(self, auth_client, db_session):
        pub, _draft, ws_post = self._seed(db_session)
        resp = auth_client.get("/feed.json")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert pub.title in body
        assert ws_post.title not in body

    def test_feed_etag_stable_when_workspace_post_added(self, auth_client, db_session):
        """INV-012: Adding a workspace post does not change the public feed ETag."""
        pub, *_ = self._seed(db_session)
        resp1 = auth_client.get("/feed.xml")
        etag1 = resp1.headers.get("ETag")

        # Add another workspace post.
        ws_owner, _ = _create_user("editor", email="ws_etag@example.com")
        ws = ws_svc.create_workspace(name="ETag WS", owner=ws_owner)
        _db.session.commit()
        extra = Post(
            title="Extra WS Post",
            slug="ws-extra",
            markdown_body="extra",
            status=PostStatus.published,
            author_id=ws_owner.id,
            workspace_id=ws.id,
            published_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(extra)
        _db.session.commit()

        resp2 = auth_client.get("/feed.xml")
        etag2 = resp2.headers.get("ETag")

        # ETag derived from public content only; workspace addition must not change it.
        assert etag1 == etag2, (
            f"ETag changed after workspace-only content change: {etag1!r} → {etag2!r}"
        )


# ── Sitemap isolation ─────────────────────────────────────────────────────────


class TestSitemapIsolation:
    """INV-009: sitemap excludes drafts and workspace posts."""

    def test_sitemap_excludes_workspace_posts(self, auth_client, db_session):
        author, _ = _create_user("editor", email="sitemap_author@example.com")
        ws_owner, _ = _create_user("editor", email="sitemap_ws_owner@example.com")
        ws = ws_svc.create_workspace(name="Sitemap WS", owner=ws_owner)
        _db.session.commit()

        pub = _make_published_post(author, slug="sitemap-pub", title="Sitemap Public")
        ws_post = Post(
            title="Sitemap Secret",
            slug="sitemap-secret",
            markdown_body="secret",
            status=PostStatus.published,
            author_id=ws_owner.id,
            workspace_id=ws.id,
            published_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(ws_post)
        _db.session.commit()

        resp = auth_client.get("/sitemap.xml")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert pub.slug in body, "Public post must appear in sitemap"
        assert ws_post.slug not in body, "Workspace post must NOT appear in sitemap"

    def test_sitemap_excludes_drafts(self, auth_client, db_session):
        author, _ = _create_user("editor", email="sitemap_d@example.com")
        draft = Post(
            title="Sitemap Draft",
            slug="sitemap-draft",
            markdown_body="",
            status=PostStatus.draft,
            author_id=author.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(draft)
        _db.session.commit()

        resp = auth_client.get("/sitemap.xml")
        body = resp.data.decode()
        assert "sitemap-draft" not in body


# ── Cache-Control headers ─────────────────────────────────────────────────────


class TestCacheControlHeaders:
    """INV-010/011: workspace routes → private; public routes → public."""

    def _setup_workspace(self, db_session):
        owner, owner_tok = _create_user("editor", email="cc_owner@example.com")
        ws = ws_svc.create_workspace(name="Cache WS", owner=owner)
        _db.session.commit()
        return ws, owner_tok

    def test_workspace_dashboard_has_private_no_store(self, auth_client, db_session):
        ws, owner_tok = self._setup_workspace(db_session)
        resp = auth_client.get(f"/w/{ws.slug}", headers=_auth(owner_tok))
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc, f"Expected 'private' in Cache-Control, got: {cc!r}"
        assert "no-store" in cc, f"Expected 'no-store' in Cache-Control, got: {cc!r}"

    def test_workspace_document_has_private_no_store(self, auth_client, db_session):
        ws, owner_tok = self._setup_workspace(db_session)
        from sqlalchemy import select

        owner_user = _db.session.scalar(
            select(User).where(User.email == "cc_owner@example.com")
        )
        doc = ws_svc.create_workspace_document(
            workspace=ws,
            author=owner_user,
            title="Cache Doc",
            markdown_body="body",
        )
        _db.session.commit()
        resp = auth_client.get(
            f"/w/{ws.slug}/docs/{doc.slug}", headers=_auth(owner_tok)
        )
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "no-store" in cc

    def test_rss_feed_has_public_cache_control(self, auth_client, db_session):
        resp = auth_client.get("/feed.xml")
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc, f"Expected 'public' in feed Cache-Control, got: {cc!r}"

    def test_sitemap_has_public_cache_control(self, auth_client, db_session):
        resp = auth_client.get("/sitemap.xml")
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc, (
            f"Expected 'public' in sitemap Cache-Control, got: {cc!r}"
        )


# ── Workspace slug isolation ──────────────────────────────────────────────────


class TestSlugNamespaceIsolation:
    """Workspace posts may reuse slugs that exist on the public layer."""

    def test_workspace_post_can_share_slug_with_public_post(self, db_session):
        """The partial unique indexes must allow the same slug in two scopes."""
        from sqlalchemy.exc import IntegrityError

        author, _ = _create_user("editor", email="slug_author@example.com")
        ws_owner, _ = _create_user("editor", email="slug_ws@example.com")
        ws = ws_svc.create_workspace(name="Slug WS", owner=ws_owner)
        _db.session.commit()

        # Create public post with slug "my-article".
        pub = Post(
            title="My Article",
            slug="my-article",
            markdown_body="",
            status=PostStatus.draft,
            author_id=author.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(pub)
        _db.session.flush()

        # Create workspace post with the SAME slug — must succeed.
        ws_doc = Post(
            title="My Article",
            slug="my-article",
            markdown_body="",
            status=PostStatus.draft,
            author_id=ws_owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(ws_doc)
        try:
            _db.session.flush()  # Should NOT raise IntegrityError
        except IntegrityError:
            pytest.fail(
                "IntegrityError: workspace post must be allowed to share a slug "
                "with a public post (partial unique index violation)"
            )

    def test_duplicate_public_slugs_rejected(self, db_session):
        """Two public posts (workspace_id NULL) with the same slug must fail."""
        from sqlalchemy.exc import IntegrityError

        author, _ = _create_user("editor", email="dup_slug@example.com")
        p1 = Post(
            title="A",
            slug="dup-slug",
            markdown_body="",
            status=PostStatus.draft,
            author_id=author.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        p2 = Post(
            title="B",
            slug="dup-slug",
            markdown_body="",
            status=PostStatus.draft,
            author_id=author.id,
            workspace_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add_all([p1, p2])
        with pytest.raises(IntegrityError):
            _db.session.flush()


# ── Shared helper ─────────────────────────────────────────────────────────────


_user_counter = {"n": 0}


def _create_user(role: str = "reader", email: str | None = None) -> tuple[User, str]:
    """Create a user with *role* and return (user, access_token).

    Uses its own counter so each call within a test creates a unique user.
    """
    from backend.services.auth_service import AuthService

    _user_counter["n"] += 1
    n = _user_counter["n"]
    email = email or f"ws_test_user_{n}@example.com"
    username = f"ws_user_{n}"
    user = AuthService.register(email, username, "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    token = AuthService.issue_access_token(user)
    return user, token
