"""Tests for WorkspaceHealthService.get_unimproved_content.

Coverage
--------
  WHUI-001  Returns empty list when all content has accepted revisions.
  WHUI-002  Returns content with zero accepted revisions.
  WHUI-003  Pending/rejected revisions do NOT count as improved.
  WHUI-004  Draft content excluded.
  WHUI-005  Content from other workspaces excluded.
  WHUI-006  Sorted: created_at ASC, id DESC.
  WHUI-007  Limit parameter respected.
  WHUI-008  Returns all kinds (prompt, article, playbook) — not filtered by kind.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(54_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"whui{n}@x.com",
        username=f"whui{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    n = _n()
    ws = Workspace(name=f"WHUI {n}", slug=f"whui-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _make_post(
    author, ws, *, kind="article", status=PostStatus.published, days_old=0
) -> Post:
    n = _n()
    ts = datetime.now(UTC) - timedelta(days=days_old)
    p = Post(
        title=f"WHUI {n}",
        slug=f"whui-{n}",
        kind=kind,
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=ws.id,
        view_count=0,
        created_at=ts,
        updated_at=ts,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_revision(post, author, *, status=RevisionStatus.accepted) -> Revision:
    r = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_id=None,
        base_version_number=1,
        proposed_markdown="improved",
        summary="fix",
        status=status,
    )
    _db.session.add(r)
    _db.session.flush()
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_whui_001_all_improved_returns_empty(db_session):
    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    p = _make_post(owner, ws)
    _make_revision(p, contrib)
    result = WorkspaceHealthService.get_unimproved_content(ws)
    assert result == []


def test_whui_002_no_accepted_revision_returned(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p = _make_post(owner, ws)
    result = WorkspaceHealthService.get_unimproved_content(ws)
    assert len(result) == 1
    assert result[0].post_id == p.id


def test_whui_003_pending_rejected_do_not_count(db_session):
    owner = _make_user()
    contrib = _make_user()
    ws = _make_workspace(owner)
    p = _make_post(owner, ws)
    _make_revision(p, contrib, status=RevisionStatus.pending)
    _make_revision(p, contrib, status=RevisionStatus.rejected)
    result = WorkspaceHealthService.get_unimproved_content(ws)
    assert len(result) == 1  # still unimproved


def test_whui_004_draft_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, status=PostStatus.draft)
    result = WorkspaceHealthService.get_unimproved_content(ws)
    assert result == []


def test_whui_005_other_workspace_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    ws2 = _make_workspace(owner)
    _make_post(owner, ws2)
    result = WorkspaceHealthService.get_unimproved_content(ws)
    assert result == []


def test_whui_006_sorted_created_at_asc_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p_older = _make_post(owner, ws, days_old=10)
    p_newer = _make_post(owner, ws, days_old=2)
    result = WorkspaceHealthService.get_unimproved_content(ws)
    assert result[0].post_id == p_older.id
    assert result[1].post_id == p_newer.id


def test_whui_007_limit_respected(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    for _ in range(5):
        _make_post(owner, ws)
    result = WorkspaceHealthService.get_unimproved_content(ws, limit=3)
    assert len(result) == 3


def test_whui_008_all_kinds_included(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    for kind in ("prompt", "article", "playbook"):
        _make_post(owner, ws, kind=kind)
    result = WorkspaceHealthService.get_unimproved_content(ws)
    kinds = {r.kind for r in result}
    assert "prompt" in kinds
    assert "article" in kinds
    assert "playbook" in kinds
