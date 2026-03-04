"""Tests for Prompt Evolution Analytics — public scope isolation.

Coverage
--------
  PAS-001  Version timeline contains no entries before any PostVersion rows exist.
  PAS-002  Fork tree excludes draft forks in public scope.
  PAS-003  Fork tree excludes workspace-scoped forks when querying public scope.
  PAS-004  Fork tree excludes forks from other workspaces in public scope.
  PAS-005  GET /prompts/<slug>/analytics returns 404 for draft prompts.
  PAS-006  GET /prompts/<slug>/analytics returns 200 for published prompts.
  PAS-007  Execution stats total_views matches Post.view_count.
  PAS-008  Execution stats views_last_30_days counts only recent AnalyticsEvents.
  PAS-009  Fork tree only includes posts linked via 'derived_from' (not other link types).
  PAS-010  Analytics endpoint does not leak workspace forks to public viewers.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.analytics import AnalyticsEvent
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services import prompt_analytics_service as svc

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"pas{n}@example.com",
        username=f"pasuser{n}",
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
    view_count: int = 0,
) -> Post:
    n = _n()
    p = Post(
        title=f"Prompt {n}",
        slug=f"prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        view_count=view_count,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_post(
    author,
    *,
    workspace_id: int | None = None,
    kind: str = "article",
    status: PostStatus = PostStatus.published,
) -> Post:
    n = _n()
    p = Post(
        title=f"Post {n}",
        slug=f"post-{n}",
        kind=kind,
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


def _make_version(post: Post, version_number: int = 1) -> PostVersion:
    pv = PostVersion(
        post_id=post.id,
        version_number=version_number,
        markdown_body="body",
        accepted_by_id=post.author_id,
    )
    _db.session.add(pv)
    _db.session.flush()
    return pv


def _add_analytics_event(post: Post, *, days_ago: int = 0):
    ev = AnalyticsEvent(
        event_type="post_view",
        post_id=post.id,
        occurred_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    _db.session.add(ev)
    _db.session.flush()
    return ev


def _login(client, user) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ──────────────────────────────────────────────────────────────────────────────
# PAS-001  Empty timeline when no PostVersion rows exist
# ──────────────────────────────────────────────────────────────────────────────


class TestVersionTimelineEmpty:
    def test_no_versions_returns_empty(self, db_session):
        """PAS-001: timeline is empty when no PostVersion rows have been committed."""
        author = _make_user()
        prompt = _make_prompt(author)
        _db.session.commit()

        timeline = svc.get_version_timeline(prompt, workspace_id=None)
        assert timeline == []


# ──────────────────────────────────────────────────────────────────────────────
# PAS-002  Fork tree excludes draft forks
# ──────────────────────────────────────────────────────────────────────────────


class TestForkTreePublicDraftExclusion:
    def test_draft_fork_excluded(self, db_session):
        """PAS-002: draft fork is not included in public fork tree."""
        author = _make_user()
        prompt = _make_prompt(author)
        draft_fork = _make_post(author, status=PostStatus.draft)
        _link_derived_from(draft_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert all(f.post_id != draft_fork.id for f in forks)

    def test_published_fork_included(self, db_session):
        """Sanity: published public fork IS returned."""
        author = _make_user()
        prompt = _make_prompt(author)
        pub_fork = _make_post(author, status=PostStatus.published)
        _link_derived_from(pub_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert any(f.post_id == pub_fork.id for f in forks)


# ──────────────────────────────────────────────────────────────────────────────
# PAS-003  Fork tree excludes workspace forks in public scope
# ──────────────────────────────────────────────────────────────────────────────


class TestForkTreePublicWorkspaceExclusion:
    def test_workspace_fork_excluded_in_public_scope(self, db_session):
        """PAS-003: workspace-scoped fork not returned in public scope."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner)
        ws_fork = _make_post(owner, workspace_id=ws.id, status=PostStatus.published)
        _link_derived_from(ws_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert all(f.post_id != ws_fork.id for f in forks)

    def test_public_fork_visible_in_public_scope(self, db_session):
        """Sanity: public fork IS still returned."""
        owner = _make_user()
        prompt = _make_prompt(owner)
        pub_fork = _make_post(owner, status=PostStatus.published)
        _link_derived_from(pub_fork, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert any(f.post_id == pub_fork.id for f in forks)


# ──────────────────────────────────────────────────────────────────────────────
# PAS-004  Fork tree excludes forks from other workspaces in public scope
# ──────────────────────────────────────────────────────────────────────────────


class TestForkTreeOtherWorkspaceExclusion:
    def test_other_workspace_fork_not_in_public_scope(self, db_session):
        """PAS-004: fork from a completely different workspace never appears in public scope."""
        owner = _make_user()
        ws_a = _make_workspace(owner)
        ws_b = _make_workspace(owner)
        prompt = _make_prompt(owner, workspace_id=ws_a.id)
        fork_b = _make_post(owner, workspace_id=ws_b.id, status=PostStatus.published)
        _link_derived_from(fork_b, prompt)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert all(f.post_id != fork_b.id for f in forks)


# ──────────────────────────────────────────────────────────────────────────────
# PAS-005 / PAS-006  Route returns 404 for draft, 200 for published
# ──────────────────────────────────────────────────────────────────────────────


class TestPublicAnalyticsRoute:
    def test_draft_returns_404(self, auth_client, db_session):
        """PAS-005: /prompts/<slug>/analytics returns 404 when prompt is draft."""
        author = _make_user()
        prompt = _make_prompt(author, status=PostStatus.draft)
        _db.session.commit()
        _login(auth_client, author)

        resp = auth_client.get(f"/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 404

    def test_published_returns_200(self, auth_client, db_session):
        """PAS-006: /prompts/<slug>/analytics returns 200 for published prompt."""
        author = _make_user()
        prompt = _make_prompt(author, status=PostStatus.published)
        _db.session.commit()

        resp = auth_client.get(f"/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 200

    def test_analytics_page_contains_prompt_title(self, auth_client, db_session):
        """PAS-006b: analytics page includes the prompt title."""
        author = _make_user()
        prompt = _make_prompt(author, status=PostStatus.published)
        _db.session.commit()

        resp = auth_client.get(f"/prompts/{prompt.slug}/analytics")
        assert prompt.title.encode() in resp.data


# ──────────────────────────────────────────────────────────────────────────────
# PAS-007 / PAS-008  Execution stats
# ──────────────────────────────────────────────────────────────────────────────


class TestExecutionStatsPublic:
    def test_total_views_matches_view_count(self, db_session):
        """PAS-007: total_views comes from Post.view_count."""
        author = _make_user()
        prompt = _make_prompt(author, view_count=42)
        _db.session.commit()

        stats = svc.get_execution_stats(prompt, workspace_id=None)
        assert stats.total_views == 42

    def test_views_last_30_days_only_recent_events(self, db_session):
        """PAS-008: views_last_30_days only counts events within the last 30 days."""
        author = _make_user()
        prompt = _make_prompt(author)
        _add_analytics_event(prompt, days_ago=5)  # recent — counts
        _add_analytics_event(prompt, days_ago=15)  # recent — counts
        _add_analytics_event(prompt, days_ago=40)  # old — excluded
        _db.session.commit()

        stats = svc.get_execution_stats(prompt, workspace_id=None)
        assert stats.views_last_30_days == 2

    def test_views_last_30_days_excludes_other_posts(self, db_session):
        """Views from a different post are not counted."""
        author = _make_user()
        prompt = _make_prompt(author)
        other = _make_prompt(author)
        _add_analytics_event(other, days_ago=1)
        _db.session.commit()

        stats = svc.get_execution_stats(prompt, workspace_id=None)
        assert stats.views_last_30_days == 0


# ──────────────────────────────────────────────────────────────────────────────
# PAS-009  Only 'derived_from' links appear as forks
# ──────────────────────────────────────────────────────────────────────────────


class TestForkTreeLinkTypeFilter:
    def test_related_link_not_a_fork(self, db_session):
        """PAS-009: 'related' link_type does not create a fork entry."""
        author = _make_user()
        prompt = _make_prompt(author)
        related = _make_post(author, status=PostStatus.published)
        link = ContentLink(
            from_post_id=related.id,
            to_post_id=prompt.id,
            link_type="related",  # NOT derived_from
        )
        _db.session.add(link)
        _db.session.commit()

        forks = svc.get_fork_tree(prompt, workspace_id=None)
        assert all(f.post_id != related.id for f in forks)


# ──────────────────────────────────────────────────────────────────────────────
# PAS-010  Route doesn't leak workspace forks in HTML
# ──────────────────────────────────────────────────────────────────────────────


class TestPublicRouteNoWorkspaceLeak:
    def test_ws_fork_title_not_in_public_analytics_html(self, auth_client, db_session):
        """PAS-010: workspace fork title does not appear in public analytics HTML."""
        owner = _make_user()
        ws = _make_workspace(owner)
        prompt = _make_prompt(owner, status=PostStatus.published)
        ws_fork = _make_post(
            owner,
            workspace_id=ws.id,
            status=PostStatus.published,
        )
        _link_derived_from(ws_fork, prompt)
        _db.session.commit()

        resp = auth_client.get(f"/prompts/{prompt.slug}/analytics")
        assert resp.status_code == 200
        # The workspace fork's title must not appear in the public analytics page
        assert ws_fork.title.encode() not in resp.data
