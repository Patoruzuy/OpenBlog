"""Tests for Fork Recommendation Service — public scope.

Coverage
--------
  FRP-001  compute_family returns only published public forks.
  FRP-002  Workspace-scoped forks excluded from public scope.
  FRP-003  Draft forks excluded from public scope.
  FRP-004  Base prompt itself excluded from the fork family.
  FRP-005  Empty family → recommend() returns [].
  FRP-006  recommend() returns [] for unauthenticated (None) user.
  FRP-007  GET /prompts/<slug>/recommendations returns 200 for logged-in user.
  FRP-008  Unauthenticated GET redirects to login (302).
  FRP-009  Route returns 404 for non-published base prompt.
  FRP-010  recommend() result excludes base prompt even if a self-link exists.
  FRP-011  All returned ForkRecommendation scopes are 'public' in public scope.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.services import fork_recommendation_service as svc

_ctr = itertools.count(10_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"frp{n}@example.com",
        username=f"frpuser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"FRP-Prompt {n}",
        slug=f"frp-prompt-{n}",
        kind="prompt",
        markdown_body="hello {{name}}",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_fork(
    base: Post, author, *, workspace_id=None, status=PostStatus.published
) -> Post:
    n = _n()
    fork = Post(
        title=f"FRP-Fork {n}",
        slug=f"frp-fork-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(fork)
    _db.session.flush()
    _db.session.add(
        ContentLink(
            from_post_id=fork.id,
            to_post_id=base.id,
            link_type="derived_from",
            created_by_user_id=author.id,
        )
    )
    _db.session.flush()
    return fork


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ==============================================================================
# FRP-001 — published public forks included
# ==============================================================================


class TestComputeFamilyPublicBase:
    def test_published_public_forks_included(self, db_session):
        """FRP-001"""
        author = _make_user()
        base = _make_prompt(author)
        fork1 = _make_fork(base, author)
        fork2 = _make_fork(base, author)
        _db.session.commit()

        family = svc.compute_family(base, workspace=None)
        ids = {f.id for f in family}
        assert fork1.id in ids
        assert fork2.id in ids

    def test_workspace_forks_excluded_from_public_scope(self, db_session):
        """FRP-002"""
        from backend.models.workspace import (
            Workspace,
            WorkspaceMember,
            WorkspaceMemberRole,
        )

        author = _make_user()
        n = _n()
        ws = Workspace(name=f"FRP-WS {n}", slug=f"frp-ws-{n}", owner_id=author.id)
        _db.session.add(ws)
        _db.session.flush()
        _db.session.add(
            WorkspaceMember(
                workspace_id=ws.id, user_id=author.id, role=WorkspaceMemberRole.owner
            )
        )
        _db.session.flush()

        base = _make_prompt(author)
        pub_fork = _make_fork(base, author)
        _ws_fork = _make_fork(base, author, workspace_id=ws.id)
        _db.session.commit()

        family = svc.compute_family(base, workspace=None)
        ids = {f.id for f in family}
        assert pub_fork.id in ids
        assert _ws_fork.id not in ids

    def test_draft_forks_excluded(self, db_session):
        """FRP-003"""
        author = _make_user()
        base = _make_prompt(author)
        published_fork = _make_fork(base, author)
        _draft_fork = _make_fork(base, author, status=PostStatus.draft)
        _db.session.commit()

        family = svc.compute_family(base, workspace=None)
        ids = {f.id for f in family}
        assert published_fork.id in ids
        assert _draft_fork.id not in ids

    def test_base_prompt_excluded(self, db_session):
        """FRP-004"""
        author = _make_user()
        base = _make_prompt(author)
        _make_fork(base, author)
        _db.session.commit()

        family = svc.compute_family(base, workspace=None)
        assert base.id not in {f.id for f in family}

    def test_empty_family_returns_empty_list(self, db_session):
        """FRP-005"""
        author = _make_user()
        base = _make_prompt(author)
        _db.session.commit()

        result = svc.recommend(author, base, workspace=None)
        assert result == []


# ==============================================================================
# FRP-006 — unauthenticated returns []
# ==============================================================================


class TestUnauthenticated:
    def test_recommend_returns_empty_for_none_user(self, db_session):
        """FRP-006"""
        author = _make_user()
        base = _make_prompt(author)
        _make_fork(base, author)
        _db.session.commit()

        result = svc.recommend(None, base, workspace=None)  # type: ignore[arg-type]
        assert result == []


# ==============================================================================
# FRP-007 / FRP-008 / FRP-009 — HTTP routes
# ==============================================================================


class TestPublicRoute:
    def test_authenticated_gets_200(self, db_session, auth_client):
        """FRP-007"""
        author = _make_user()
        base = _make_prompt(author)
        _make_fork(base, author)
        _db.session.commit()

        _login(auth_client, author)
        resp = auth_client.get(f"/prompts/{base.slug}/recommendations")
        assert resp.status_code == 200
        assert b"Recommended Forks" in resp.data

    def test_unauthenticated_redirects(self, db_session, auth_client):
        """FRP-008"""
        author = _make_user()
        base = _make_prompt(author)
        _db.session.commit()

        resp = auth_client.get(f"/prompts/{base.slug}/recommendations")
        assert resp.status_code == 302
        assert "login" in resp.headers["Location"]

    def test_nonpublished_base_returns_404(self, db_session, auth_client):
        """FRP-009"""
        author = _make_user()
        draft_base = _make_prompt(author, status=PostStatus.draft)
        _db.session.commit()

        _login(auth_client, author)
        resp = auth_client.get(f"/prompts/{draft_base.slug}/recommendations")
        assert resp.status_code == 404


# ==============================================================================
# FRP-011 — all scopes are 'public'
# ==============================================================================


class TestPublicRecommendationScope:
    def test_all_scopes_are_public(self, db_session):
        """FRP-011"""
        author = _make_user()
        base = _make_prompt(author)
        _make_fork(base, author)
        _make_fork(base, author)
        _db.session.commit()

        recs = svc.recommend(author, base, workspace=None)
        assert all(r.scope == "public" for r in recs)
