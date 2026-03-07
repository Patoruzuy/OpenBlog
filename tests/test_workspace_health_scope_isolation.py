"""Tests for Workspace Health Dashboard — scope isolation.

Coverage
--------
  WHSI-001  Non-member gets 404.
  WHSI-002  Unauthenticated user gets redirect to login (302).
  WHSI-003  Member gets 200.
  WHSI-004  Workspace A metrics do not bleed into Workspace B.
  WHSI-005  Public posts (workspace_id=NULL) do NOT appear in any metric.
  WHSI-006  Cache-Control header is private, no-store.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

_ctr = itertools.count(57_000)


def _n() -> int:
    return next(_ctr)


def _make_workspace(owner):
    n = _n()
    ws = Workspace(name=f"WHSI {n}", slug=f"whsi-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    _db.session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=owner.id, role=WorkspaceMemberRole.owner
        )
    )
    _db.session.flush()
    return ws


def _make_post(author, ws, *, kind="prompt", days_old=0) -> Post:
    n = _n()
    ts = datetime.now(UTC) - timedelta(days=days_old)
    p = Post(
        title=f"WHSI {n}",
        slug=f"whsi-{n}",
        kind=kind,
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=ws.id if ws else None,
        view_count=0,
        updated_at=ts,
        created_at=ts,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


# ── Route-layer tests ─────────────────────────────────────────────────────────


def test_whsi_001_non_member_gets_404(auth_client, make_user_token):
    owner, owner_tok = make_user_token("whsi-owner@x.com", "whsi_owner")
    outsider, outsider_tok = make_user_token("whsi-out@x.com", "whsi_out")
    ws = _make_workspace(owner)
    _db.session.commit()
    resp = auth_client.get(
        f"/w/{ws.slug}/health",
        headers={"Authorization": f"Bearer {outsider_tok}"},
    )
    assert resp.status_code == 404


def test_whsi_002_unauthenticated_redirects_to_login(auth_client, make_user_token):
    owner, _ = make_user_token("whsi-owner2@x.com", "whsi_owner2")
    ws = _make_workspace(owner)
    _db.session.commit()
    resp = auth_client.get(f"/w/{ws.slug}/health")
    assert resp.status_code in (301, 302)
    assert "login" in resp.headers["Location"]


def test_whsi_003_member_gets_200(auth_client, make_user_token):
    owner, owner_tok = make_user_token("whsi-owner3@x.com", "whsi_owner3")
    ws = _make_workspace(owner)
    _db.session.commit()
    resp = auth_client.get(
        f"/w/{ws.slug}/health",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert resp.status_code == 200


def test_whsi_006_cache_control_header(auth_client, make_user_token):
    owner, owner_tok = make_user_token("whsi-owner6@x.com", "whsi_owner6")
    ws = _make_workspace(owner)
    _db.session.commit()
    resp = auth_client.get(
        f"/w/{ws.slug}/health",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert resp.status_code == 200
    cc = resp.headers.get("Cache-Control", "")
    assert "no-store" in cc
    assert "private" in cc


# ── Service-layer isolation tests ─────────────────────────────────────────────


def test_whsi_004_workspace_metrics_isolated(db_session):
    from backend.models.user import User, UserRole

    n = _n()
    owner = User(
        email=f"whsi-iso{n}@x.com",
        username=f"whsiiso{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(owner)
    _db.session.flush()

    ws_a = _make_workspace(owner)
    ws_b = _make_workspace(owner)

    # Post in ws_a
    _make_post(owner, ws_a, kind="prompt")
    _db.session.flush()

    from backend.services.workspace_health_service import (
        WorkspaceHealthService,  # noqa: PLC0415
    )

    summary_a = WorkspaceHealthService.get_health_summary(ws_a)
    summary_b = WorkspaceHealthService.get_health_summary(ws_b)

    assert summary_a.total_prompts == 1
    assert summary_b.total_prompts == 0


def test_whsi_005_public_posts_not_in_workspace_metrics(db_session):
    from backend.models.user import User, UserRole

    n = _n()
    owner = User(
        email=f"whsi-pub{n}@x.com",
        username=f"whsipub{n}",
        password_hash="x",
        role=UserRole.reader,
    )
    _db.session.add(owner)
    _db.session.flush()

    ws = _make_workspace(owner)
    # Create a public post (workspace_id=None)
    _make_post(owner, None, kind="prompt")
    _db.session.flush()

    from backend.services.workspace_health_service import (
        WorkspaceHealthService,  # noqa: PLC0415
    )

    summary = WorkspaceHealthService.get_health_summary(ws)

    # Public post must NOT appear in workspace metrics
    assert summary.total_prompts == 0
