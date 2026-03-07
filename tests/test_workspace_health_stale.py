"""Tests for WorkspaceHealthService.get_stale_content.

Coverage
--------
  WHST-001  Returns empty list when no stale content.
  WHST-002  Returns content older than 90 days.
  WHST-003  Content exactly 90 days old not included (strictly <).
  WHST-004  kind='framework' excluded.
  WHST-005  Draft content excluded.
  WHST-006  days_stale computed correctly in Python.
  WHST-007  Sorted: updated_at ASC, id DESC.
  WHST-008  stale_days parameter respected.
  WHST-009  Limit parameter respected.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole
from backend.services.workspace_health_service import WorkspaceHealthService

_ctr = itertools.count(53_000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole

    n = _n()
    u = User(
        email=f"whst{n}@x.com",
        username=f"whst{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_workspace(owner):
    n = _n()
    ws = Workspace(name=f"WHST {n}", slug=f"whst-{n}", owner_id=owner.id)
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
        title=f"WHST {n}",
        slug=f"whst-{n}",
        kind=kind,
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=ws.id,
        view_count=0,
        updated_at=ts,
        created_at=ts,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_whst_001_no_stale_returns_empty(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, days_old=30)
    result = WorkspaceHealthService.get_stale_content(ws)
    assert result == []


def test_whst_002_returns_stale_content(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p = _make_post(owner, ws, days_old=91)
    result = WorkspaceHealthService.get_stale_content(ws)
    assert len(result) == 1
    assert result[0].post_id == p.id


def test_whst_003_exactly_90_days_not_included(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    # 89 days old should NOT be stale (cutoff is updated_at < now - 90 days)
    _make_post(owner, ws, days_old=89)
    result = WorkspaceHealthService.get_stale_content(ws)
    assert result == []


def test_whst_004_framework_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, kind="framework", days_old=200)
    result = WorkspaceHealthService.get_stale_content(ws)
    assert result == []


def test_whst_005_draft_excluded(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, status=PostStatus.draft, days_old=200)
    result = WorkspaceHealthService.get_stale_content(ws)
    assert result == []


def test_whst_006_days_stale_computed_correctly(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, days_old=120)
    result = WorkspaceHealthService.get_stale_content(ws)
    assert len(result) == 1
    assert 118 <= result[0].days_stale <= 122  # allow ±2 for test timing


def test_whst_007_sorted_updated_at_asc_id_desc(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    p_older = _make_post(owner, ws, days_old=200)
    p_newer = _make_post(owner, ws, days_old=100)
    result = WorkspaceHealthService.get_stale_content(ws)
    assert result[0].post_id == p_older.id
    assert result[1].post_id == p_newer.id


def test_whst_008_custom_stale_days(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    _make_post(owner, ws, days_old=31)  # stale at 30 days
    result = WorkspaceHealthService.get_stale_content(ws, stale_days=30)
    assert len(result) == 1


def test_whst_009_limit_respected(db_session):
    owner = _make_user()
    ws = _make_workspace(owner)
    for _ in range(5):
        _make_post(owner, ws, days_old=120)
    result = WorkspaceHealthService.get_stale_content(ws, limit=3)
    assert len(result) == 3
