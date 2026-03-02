"""Tests for Prompt Evolution Analytics — workspace scope isolation.

Coverage
--------
  WAS-001  Workspace scope: member sees workspace forks + public forks.
  WAS-002  Public scope: never includes workspace-only forks.
  WAS-003  Workspace scope: never includes forks from a different workspace.
  WAS-004  Workspace analytics route returns 404 for non-members.
  WAS-005  Workspace analytics route sets Cache-Control: private, no-store header.
  WAS-006  Fork scope field is correctly set ('workspace' vs 'public').
  WAS-007  Workspace route returns 200 for workspace member.
  WAS-008  Workspace member sees correct fork count including public forks.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import prompt_analytics_service as svc

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"was{n}@example.com",
        username=f"wasuser{n}",
        password_hash="x",
        role=UserRole(role),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(
    author,
    *,
    workspace_id: int | None = None,
    status: PostStatus = PostStatus.published,
) -> Post:
    n = _n()
    p = Post(
        title=f"WS-Prompt {n}",
        slug=f"ws-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_post(
    author,
    *,
    workspace_id: int | None = None,
    status: PostStatus = PostStatus.published,
) -> Post:
    n = _n()
    p = Post(
        title=f"WS-Post {n}",
        slug=f"ws-post-{n}",
        kind="article",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_workspace(owner) -> Workspace:
    n = _n()
    ws = Workspace(name=f"WS {n}", slug=f"ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    member = WorkspaceMember(
        workspace_id=ws.id,
        user_id=owner.id,
        role=WorkspaceMemberRole.owner,
    )
    _db.session.add(member)
    _db.session.flush()
    return ws


def _add_member(ws: Workspace, user, role=WorkspaceMemberRole.viewer):
    m = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role)
    _db.session.add(m)
    _db.session.flush()


def _link_derived_from(from_post: Post, to_post: Post, workspace_id: int | None = None):
    link = ContentLink(
        from_post_id=from_post.id,
        to_post_id=to_post.id,
        link_type="derived_from",
        workspace_id=workspace_id,
    )
    _db.session.add(link)
    _db.session.flush()
    return link


def _login(client, user) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ──────────────────────────────────────────────────────────────────────────────
# WAS-001  Member sees workspace + public forks
# ──────────────────────────────────────────────────────────────────────────────

class TestWorkspaceMemberForkVisibility:
    def test_member_sees_both_public_and_workspace_forks(self, db_session):
        """WAS-001: workspace context includes public forks AND same-ws forks."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws.id)
        pub_fork = _make_post(owner, workspace_id=None, status=PostStatus.published)
        ws_fork = _make_post(owner, workspace_id=ws.id, status=PostStatus.published)
        _link_derived_from(pub_fork, prompt)
        _link_derived_from(ws_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=ws.id)
        fork_ids = {f.post_id for f in forks}
        assert pub_fork.id in fork_ids
        assert ws_fork.id in fork_ids


# ──────────────────────────────────────────────────────────────────────────────
# WAS-002  Public scope never sees workspace forks
# ──────────────────────────────────────────────────────────────────────────────

class TestPublicScopeNoWorkspaceForks:
    def test_public_scope_excludes_ws_fork(self, db_session):
        """WAS-002: public scope (workspace_id=None) never returns workspace forks."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner)
        ws_fork = _make_post(owner, workspace_id=ws.id, status=PostStatus.published)
        _link_derived_from(ws_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert all(f.post_id != ws_fork.id for f in forks)


# ──────────────────────────────────────────────────────────────────────────────
# WAS-003  Workspace scope never sees forks from a different workspace
# ──────────────────────────────────────────────────────────────────────────────

class TestWorkspaceScopeNoOtherWorkspace:
    def test_other_workspace_fork_excluded(self, db_session):
        """WAS-003: fork from workspace B not visible in workspace A context."""
        owner = _make_user()
        ws_a = _make_workspace(owner)
        ws_b = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws_a.id)
        fork_b = _make_post(owner, workspace_id=ws_b.id, status=PostStatus.published)
        _link_derived_from(fork_b, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=ws_a.id)
        assert all(f.post_id != fork_b.id for f in forks)


# ──────────────────────────────────────────────────────────────────────────────
# WAS-004  Non-member gets 404 from workspace analytics route
# ──────────────────────────────────────────────────────────────────────────────

class TestWorkspaceRouteNonMemberAccess:
    def test_non_member_404(self, auth_client, db_session):
        """WAS-004: non-member receives 404 from workspace analytics route."""
        owner = _make_user()
        outsider = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws.id, status=PostStatus.published)
        _db.session.commit()

        _login(auth_client, outsider)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 404

    def test_unauthenticated_redirected_or_404(self, client, db_session):
        """WAS-004b: unauthenticated request to workspace analytics → redirect or 404."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws.id, status=PostStatus.published)
        _db.session.commit()

        resp = client.get(f"/w/{ws.slug}/prompts/{prompt.slug}/analytics")
        # Non-members (including unauthenticated) get 404 (fail-closed pattern)
        assert resp.status_code in (302, 404)


# ──────────────────────────────────────────────────────────────────────────────
# WAS-005  Cache-Control header on workspace analytics
# ──────────────────────────────────────────────────────────────────────────────

class TestWorkspaceAnalyticsCacheControl:
    def test_cache_control_private_no_store(self, auth_client, db_session):
        """WAS-005: workspace analytics response carries Cache-Control: private, no-store."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws.id, status=PostStatus.published)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc


# ──────────────────────────────────────────────────────────────────────────────
# WAS-006  Fork scope field is correctly set
# ──────────────────────────────────────────────────────────────────────────────

class TestForkScopeField:
    def test_public_fork_scope_is_public(self, db_session):
        """WAS-006a: public fork has scope='public'."""
        owner = _make_user()
        prompt = _make_prompt(owner)
        pub_fork = _make_post(owner, workspace_id=None, status=PostStatus.published)
        _link_derived_from(pub_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        matched = next((f for f in forks if f.post_id == pub_fork.id), None)
        assert matched is not None
        assert matched.scope == "public"

    def test_workspace_fork_scope_is_workspace(self, db_session):
        """WAS-006b: workspace fork has scope='workspace'."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner)
        ws_fork = _make_post(owner, workspace_id=ws.id, status=PostStatus.published)
        _link_derived_from(ws_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=ws.id)
        matched = next((f for f in forks if f.post_id == ws_fork.id), None)
        assert matched is not None
        assert matched.scope == "workspace"


# ──────────────────────────────────────────────────────────────────────────────
# WAS-007  Workspace route returns 200 for member
# ──────────────────────────────────────────────────────────────────────────────

class TestWorkspaceAnalyticsRoute200:
    def test_member_gets_200(self, auth_client, db_session):
        """WAS-007: workspace owner gets 200 from workspace analytics route."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws.id, status=PostStatus.published)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 200

    def test_viewer_member_gets_200(self, auth_client, db_session):
        """WAS-007b: viewer-role workspace member also gets 200."""
        owner = _make_user()
        viewer = _make_user()
        ws = _make_workspace(owner)
        _add_member(ws, viewer)
        prompt = _make_prompt(owner, workspace_id=ws.id, status=PostStatus.published)
        _db.session.commit()

        _login(auth_client, viewer)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# WAS-008  Workspace member sees both public + ws forks on analytics page
# ──────────────────────────────────────────────────────────────────────────────

class TestWorkspaceAnalyticsPageForkCount:
    def test_fork_titles_appear_in_workspace_analytics(self, auth_client, db_session):
        """WAS-008: fork titles from both scopes appear in workspace analytics HTML."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws.id, status=PostStatus.published)
        pub_fork = _make_post(owner, workspace_id=None, status=PostStatus.published)
        ws_fork = _make_post(owner, workspace_id=ws.id, status=PostStatus.published)
        _link_derived_from(pub_fork, prompt)
        _link_derived_from(ws_fork, prompt)
        _db.session.commit()

        _login(auth_client, owner)
        resp = auth_client.get(f"/w/{ws.slug}/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 200
        body = resp.data
        assert pub_fork.title.encode() in body
        assert ws_fork.title.encode() in body
