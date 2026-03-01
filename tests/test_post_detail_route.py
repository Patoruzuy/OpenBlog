"""Regression tests for the /posts/<slug> SSR route.

These tests guard against the `posts.post_detail` endpoint being accidentally
removed or un-registered, which previously caused BuildError crashes across
multiple templates (search results, bookmarks, admin, profile).
"""

from __future__ import annotations

from flask import url_for

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.user import User, UserRole

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_user(db, *, role: str = "contributor") -> User:
    u = User(
        email=f"{role}@test.com",
        username=f"u_{role}",
        password_hash="x",
        role=UserRole(role),
    )
    db.session.add(u)
    db.session.commit()
    return u


def _make_post(
    db,
    author: User,
    *,
    slug: str = "hello-world",
    status: PostStatus = PostStatus.published,
) -> Post:
    post = Post(
        title="Hello World",
        slug=slug,
        markdown_body="# Hi",
        author_id=author.id,
        status=status,
    )
    db.session.add(post)
    db.session.commit()
    return post


def _login(client, user: User) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ─────────────────────────────────────────────────────────────────────────────
# url_for resolution — catches registration/decorator problems immediately
# ─────────────────────────────────────────────────────────────────────────────


class TestPostDetailUrlFor:
    def test_url_for_resolves(self, app, db_session):
        """url_for('posts.post_detail', slug='x') must not raise BuildError."""
        with app.test_request_context():
            url = url_for("posts.post_detail", slug="x")
        assert url == "/posts/x"

    def test_endpoint_name(self, app, db_session):
        """Endpoint must be named 'posts.post_detail', not 'admin.post_detail'."""
        with app.test_request_context():
            url = url_for("posts.post_detail", slug="any-slug")
        assert url.startswith("/posts/")


# ─────────────────────────────────────────────────────────────────────────────
# GET /posts/<slug> — happy paths
# ─────────────────────────────────────────────────────────────────────────────


class TestPostDetailRendering:
    def test_published_post_returns_200(self, client, db_session):
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author)
        resp = client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200

    def test_post_title_in_body(self, client, db_session):
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author)
        body = client.get(f"/posts/{post.slug}").data.decode()
        assert post.title in body

    def test_nonexistent_slug_returns_404(self, client, db_session):
        resp = client.get("/posts/does-not-exist-xyz")
        assert resp.status_code == 404

    def test_draft_hidden_from_anonymous_visitor(self, client, db_session):
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, slug="my-draft", status=PostStatus.draft)
        resp = client.get(f"/posts/{post.slug}")
        assert resp.status_code == 404

    def test_draft_visible_to_author(self, client, db_session):
        author = _make_user(_db, role="contributor")
        post = _make_post(_db, author, slug="author-draft", status=PostStatus.draft)
        _login(client, author)
        resp = client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200

    def test_draft_visible_to_editor(self, client, db_session):
        author = _make_user(_db, role="contributor")
        editor = User(
            email="ed@test.com",
            username="uu_editor",
            password_hash="x",
            role=UserRole.editor,
        )
        _db.session.add(editor)
        _db.session.commit()
        post = _make_post(_db, author, slug="editor-draft", status=PostStatus.draft)
        _login(client, editor)
        resp = client.get(f"/posts/{post.slug}")
        assert resp.status_code == 200

    def test_draft_hidden_from_other_contributor(self, client, db_session):
        author = _make_user(_db, role="contributor")
        other = User(
            email="other@test.com",
            username="uu_other",
            password_hash="x",
            role=UserRole.contributor,
        )
        _db.session.add(other)
        _db.session.commit()
        post = _make_post(_db, author, slug="other-draft", status=PostStatus.draft)
        _login(client, other)
        resp = client.get(f"/posts/{post.slug}")
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Templates that embed url_for('posts.post_detail') must not crash
# ─────────────────────────────────────────────────────────────────────────────


class TestTemplatesUsingPostDetailUrl:
    """Regression: any template rendering 'posts.post_detail' must not raise
    BuildError.  Previously caused by the missing @ssr_posts_bp.get decorator.
    """

    def test_search_results_page_renders(self, client, db_session):
        """GET /search/?q=... must render without BuildError."""
        author = _make_user(_db, role="contributor")
        _make_post(_db, author, slug="searchable-post")
        resp = client.get("/search/?q=hello")
        # 200 if results found, or still 200 for empty results — not 500
        assert resp.status_code == 200

    def test_bookmarks_page_renders_for_logged_in_user(self, client, db_session):
        """GET /bookmarks must render without BuildError."""
        user = _make_user(_db)
        _login(client, user)
        resp = client.get("/bookmarks/", follow_redirects=True)
        assert resp.status_code == 200
