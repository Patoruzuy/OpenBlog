"""Unit tests for the SSR drafts dashboard (GET /drafts/, POST /drafts/<slug>/delete)."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.post import Post, PostStatus

# ── Helpers ────────────────────────────────────────────────────────────────────


def _login(client, user_id: int) -> None:
    """Inject a Flask session cookie to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_draft(author, title: str = "My Draft", slug: str | None = None) -> Post:
    post = Post(
        title=title,
        slug=slug or title.lower().replace(" ", "-"),
        markdown_body="Draft content.",
        author_id=author.id,
        status=PostStatus.draft,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):  # noqa: ARG001
    user, _tok = make_user_token("alice@drafts-ssr.com", "alice_dr")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):  # noqa: ARG001
    user, _tok = make_user_token("bob@drafts-ssr.com", "bob_dr")
    return user


# ── GET /drafts/ ───────────────────────────────────────────────────────────────


class TestDraftsIndex:
    def test_anonymous_redirects_to_login(self, auth_client, db_session):  # noqa: ARG001
        resp = auth_client.get("/drafts/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_authenticated_returns_200(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/")
        assert resp.status_code == 200

    def test_content_type_is_html(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/")
        assert "text/html" in resp.content_type

    def test_empty_state_when_no_drafts(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/")
        # Template says "No drafts yet" when there are no drafts.
        assert b"No drafts yet" in resp.data

    def test_shows_own_draft_title(self, auth_client, alice):
        _add_draft(alice, title="Alice Vision Post", slug="alice-vision-post")
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/")
        assert b"Alice Vision Post" in resp.data

    def test_does_not_show_other_users_drafts(self, auth_client, alice, bob):
        _add_draft(bob, title="Bob Secret Draft", slug="bob-secret-draft")
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/")
        assert b"Bob Secret Draft" not in resp.data

    def test_does_not_show_published_posts(self, auth_client, alice):
        from backend.models.post import PostStatus as PS

        pub = Post(
            title="Published Article",
            slug="published-article",
            markdown_body="Content.",
            author_id=alice.id,
            status=PS.published,
        )
        db.session.add(pub)
        db.session.commit()
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/")
        assert b"Published Article" not in resp.data

    def test_multiple_drafts_listed(self, auth_client, alice):
        _add_draft(alice, title="Draft Alpha", slug="draft-alpha")
        _add_draft(alice, title="Draft Beta", slug="draft-beta")
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/")
        assert b"Draft Alpha" in resp.data
        assert b"Draft Beta" in resp.data

    # ── Search filter ──────────────────────────────────────────────────────

    def test_search_returns_matching_draft(self, auth_client, alice):
        _add_draft(alice, title="Unique Wombat Post", slug="unique-wombat-post")
        _add_draft(alice, title="Other Post", slug="other-post")
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/?search=Wombat")
        assert b"Unique Wombat Post" in resp.data
        assert b"Other Post" not in resp.data

    def test_search_empty_result_shows_no_drafts(self, auth_client, alice):
        _add_draft(alice, title="My Draft", slug="my-draft")
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/?search=xyzzy")
        assert b"No drafts matching" in resp.data

    # ── Pagination ─────────────────────────────────────────────────────────

    def test_pagination_param_accepted(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.get("/drafts/?page=1")
        assert resp.status_code == 200


# ── POST /drafts/<slug>/delete ─────────────────────────────────────────────────


class TestDeleteDraft:
    def test_owner_can_delete_own_draft(self, auth_client, alice, app):
        draft = _add_draft(alice, title="Deletable Draft", slug="deletable-draft")
        _login(auth_client, alice.id)
        resp = auth_client.post(
            f"/drafts/{draft.slug}/delete",
            data={"csrf_token": _get_csrf(auth_client)},
        )
        # Expect a redirect back to /drafts/
        assert resp.status_code == 302
        assert "/drafts" in resp.headers["Location"]
        # Confirm deleted from DB
        with app.app_context():
            from backend.extensions import db as _db
            from backend.models.post import Post as P2

            found = _db.session.get(P2, draft.id)
            assert found is None

    def test_non_owner_gets_404(self, auth_client, alice, bob):
        draft = _add_draft(alice, title="Alice Only", slug="alice-only-draft")
        _login(auth_client, bob.id)
        resp = auth_client.post(
            f"/drafts/{draft.slug}/delete",
            data={"csrf_token": _get_csrf(auth_client)},
        )
        assert resp.status_code == 404

    def test_anonymous_delete_redirects(self, auth_client, alice):
        draft = _add_draft(alice, title="Anon Delete", slug="anon-delete-draft")
        resp = auth_client.post(f"/drafts/{draft.slug}/delete")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_delete_nonexistent_slug_gives_404(self, auth_client, alice):
        _login(auth_client, alice.id)
        resp = auth_client.post("/drafts/does-not-exist/delete")
        assert resp.status_code == 404

    def test_after_delete_draft_no_longer_shown(self, auth_client, alice):
        draft = _add_draft(alice, title="Gone Draft", slug="gone-draft")
        _login(auth_client, alice.id)
        auth_client.post(
            f"/drafts/{draft.slug}/delete",
            data={"csrf_token": _get_csrf(auth_client)},
        )
        resp = auth_client.get("/drafts/")
        assert b"Gone Draft" not in resp.data


# ── Utility ────────────────────────────────────────────────────────────────────


def _get_csrf(client) -> str:
    """Return a valid CSRF token from an authenticated GET /drafts/ response.

    In TestingConfig WTF_CSRF_ENABLED is False so any non-empty string works,
    but the form still includes the hidden input on a real render.  We just
    pass an empty string to keep it simple.
    """
    return ""
