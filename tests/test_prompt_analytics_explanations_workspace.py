"""Tests for Analytics Explanations — workspace scope.

Covers:
  PAE-WS-001  Non-member GET workspace analytics → 404.
  PAE-WS-002  Non-member service call → ExplainError(404).
  PAE-WS-003  Member POST → explanation completed (CELERY_TASK_ALWAYS_EAGER).
  PAE-WS-004  Viewer member can also request an explanation.
  PAE-WS-005  GET workspace analytics has Cache-Control: private, no-store.
  PAE-WS-006  GET workspace analytics renders AI Analytics Explanations section.
  PAE-WS-007  POST workspace explain route → Cache-Control: private, no-store.
  PAE-WS-008  Unauthenticated POST workspace explain → redirect to login.
  PAE-WS-009  service: version_diff kind completes for workspace prompt.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from backend.extensions import db as _db
from backend.models.analytics_explanation import (
    AnalyticsExplanation,
    AnalyticsExplanationStatus,
)
from backend.models.post import Post, PostStatus
from backend.models.workspace import WorkspaceMemberRole
from backend.services import prompt_analytics_explain_service as explain_svc
from backend.services import workspace_service as ws_svc

# ── module-level counter ──────────────────────────────────────────────────────

_counter = itertools.count(1)


def _uid() -> int:
    return next(_counter)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_user(make_user_token, role: str = "reader"):
    n = _uid()
    return make_user_token(f"ws_expl_{n}@example.com", f"ws_expl_{n}", role=role)


def _workspace_with_owner(make_user_token):
    """Create workspace + owner; return (ws, owner, token)."""
    owner, tok = _new_user(make_user_token, role="editor")
    ws = ws_svc.create_workspace(name=f"Expl-WS-{_uid()}", owner=owner)
    _db.session.commit()
    return ws, owner, tok


def _ws_prompt(ws, author) -> Post:
    """Create a workspace prompt (kind='prompt', published) and commit."""
    uid = _uid()
    post = ws_svc.create_workspace_document(
        workspace=ws,
        author=author,
        title=f"WS Prompt {uid}",
        markdown_body="# Workspace Prompt\n\nContent for analytics.",
    )
    post.kind = "prompt"
    post.status = PostStatus.published
    _db.session.commit()
    return post


# ==============================================================================
# PAE-WS-001: HTTP non-member → 404
# ==============================================================================


class TestWorkspaceExplainNonMember:
    """Non-members are completely blocked from workspace routes."""

    def test_non_member_get_ws_analytics_returns_404(
        self, db_session, make_user_token, client
    ):
        """PAE-WS-001: non-member GET workspace analytics → 404."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        outsider, outsider_tok = _new_user(make_user_token)

        resp = client.get(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics",
            headers=_auth(outsider_tok),
        )

        assert resp.status_code == 404

    def test_non_member_post_ws_explain_returns_404(
        self, db_session, make_user_token, client
    ):
        """PAE-WS-001b: non-member POST workspace explain → 404."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        outsider, outsider_tok = _new_user(make_user_token)

        resp = client.post(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics/explain/trend",
            headers=_auth(outsider_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 404

    def test_service_non_member_raises_explain_error_404(
        self, db_session, make_user_token
    ):
        """PAE-WS-002: service raises ExplainError(404) for non-member."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)
        outsider, _ = _new_user(make_user_token)

        with pytest.raises(explain_svc.ExplainError) as exc_info:
            explain_svc.request_explanation(
                user=outsider, post=prompt, workspace=ws, kind="trend"
            )

        assert exc_info.value.status_code == 404


# ==============================================================================
# PAE-WS-003 – PAE-WS-004: Member access
# ==============================================================================


class TestWorkspaceExplainMemberAccess:
    """Members at any role can generate explanations."""

    def test_owner_post_explanation_completes(
        self, db_session, make_user_token, client
    ):
        """PAE-WS-003: workspace owner POST → explanation completed."""
        ws, owner, owner_tok = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        resp = client.post(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics/explain/trend",
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        # Should redirect to workspace analytics page.
        assert resp.status_code == 302
        assert f"/w/{ws.slug}/prompts/{prompt.slug}/analytics" in resp.headers.get(
            "Location", ""
        )

        row = _db.session.scalar(
            _db.select(AnalyticsExplanation).where(
                AnalyticsExplanation.prompt_post_id == prompt.id,
                AnalyticsExplanation.kind == "trend",
            )
        )
        assert row is not None
        _db.session.refresh(row)
        assert row.status == AnalyticsExplanationStatus.completed.value
        assert row.scope_type == "workspace"
        assert row.workspace_id == ws.id

    def test_viewer_member_can_request_explanation(self, db_session, make_user_token):
        """PAE-WS-004: viewer-role member can call request_explanation."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        viewer, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        row = explain_svc.request_explanation(
            user=viewer, post=prompt, workspace=ws, kind="fork_rationale"
        )

        _db.session.expire(row)
        assert row.status == AnalyticsExplanationStatus.completed.value
        assert row.created_by_user_id == viewer.id


# ==============================================================================
# PAE-WS-005 – PAE-WS-007: Cache-Control headers
# ==============================================================================


class TestWorkspaceExplainCacheHeaders:
    """Workspace routes must respond with Cache-Control: private, no-store."""

    def test_ws_analytics_get_has_no_store_header(
        self, db_session, make_user_token, client
    ):
        """PAE-WS-005: GET workspace analytics → Cache-Control: private, no-store."""
        ws, owner, owner_tok = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        resp = client.get(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics",
            headers=_auth(owner_tok),
        )

        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert "private" in cc

    def test_ws_explain_post_redirect_has_no_store_header(
        self, db_session, make_user_token, client
    ):
        """PAE-WS-007: POST explain redirect → Cache-Control: private, no-store."""
        ws, owner, owner_tok = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        resp = client.post(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics/explain/trend",
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 302
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc

    def test_unauthenticated_ws_explain_post_redirects_to_login(
        self, db_session, make_user_token, client
    ):
        """PAE-WS-008: unauthenticated POST workspace explain → redirect to login."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        resp = client.post(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics/explain/trend",
            follow_redirects=False,
        )

        # Route redirects to login when user is None.
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


# ==============================================================================
# PAE-WS-006: Template rendering
# ==============================================================================


class TestWorkspaceAnalyticsTemplateRendering:
    """GET workspace analytics page renders the explanation section."""

    def test_analytics_page_renders_explanation_section_when_logged_in(
        self, db_session, make_user_token, client
    ):
        """PAE-WS-006: analytics page includes AI Analytics Explanations heading."""
        ws, owner, owner_tok = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        # Generate explanation first.
        explain_svc.request_explanation(
            user=owner, post=prompt, workspace=ws, kind="trend"
        )

        resp = client.get(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics",
            headers=_auth(owner_tok),
        )

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "AI Analytics Explanations" in body


# ==============================================================================
# PAE-WS-009: version_diff kind
# ==============================================================================


class TestWorkspaceVersionDiffExplanation:
    """version_diff kind generates an explanation using PostVersion diffs."""

    def test_version_diff_explanation_completes(self, db_session, make_user_token):
        """PAE-WS-009: version_diff kind completes for workspace prompt."""
        from backend.models.post_version import PostVersion

        ws, owner, _ = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        # Create two PostVersion rows so a diff is available.
        pv1 = PostVersion(
            post_id=prompt.id,
            version_number=1,
            markdown_body="# Original\n\nFirst version content.",
            accepted_by_id=owner.id,
            created_at=datetime.now(UTC),
        )
        pv2 = PostVersion(
            post_id=prompt.id,
            version_number=2,
            markdown_body="# Updated\n\nSecond version with changes.",
            accepted_by_id=owner.id,
            created_at=datetime.now(UTC),
        )
        _db.session.add_all([pv1, pv2])
        _db.session.commit()

        row = explain_svc.request_explanation(
            user=owner, post=prompt, workspace=ws, kind="version_diff"
        )

        _db.session.expire(row)
        assert row.status == AnalyticsExplanationStatus.completed.value
        assert row.explanation_md is not None
        assert "mock" in row.explanation_md.lower()
