"""Tests for Analytics Explanations — public scope.

Covers:
  PAE-PUB-001  Unauthenticated POST → redirect to login (require_auth).
  PAE-PUB-002  Authenticated POST kind='trend' → explanation completed
               (CELERY_TASK_ALWAYS_EAGER + mock provider).
  PAE-PUB-003  Authenticated GET analytics page renders AI explanation section.
  PAE-PUB-004  POST with invalid kind → flash error + redirect back.
  PAE-PUB-005  Service: unauthenticated call raises ExplainError(401).
  PAE-PUB-006  Service: request_explanation with kind='fork_rationale' completes.
  PAE-PUB-007  Service: get_explanation returns None before any request.
  PAE-PUB-008  Service: get_explanation returns completed row after request.
  PAE-PUB-009  Feed and sitemap do not expose explanation content.
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
from backend.services import prompt_analytics_explain_service as explain_svc

# ── module-level counter for unique slugs / emails ────────────────────────────

_counter = itertools.count(1)


def _uid() -> int:
    return next(_counter)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_user(make_user_token, role: str = "reader"):
    n = _uid()
    return make_user_token(f"pub_expl_{n}@example.com", f"pub_expl_{n}", role=role)


def _public_prompt(author) -> Post:
    """Create a published public prompt (workspace_id=None, kind='prompt')."""
    uid = _uid()
    post = Post(
        title=f"Public Prompt {uid}",
        slug=f"pub-prompt-{uid}",
        kind="prompt",
        markdown_body="# Test Prompt\n\nThis is a test prompt for analytics.",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _db.session.add(post)
    _db.session.flush()
    return post


# ==============================================================================
# PAE-PUB-001 – PAE-PUB-004: HTTP route tests
# ==============================================================================


class TestPublicExplainRouteHTTP:
    """HTTP-level tests for POST /prompts/<slug>/analytics/explain/<kind>."""

    def test_unauthenticated_post_redirects_to_login(
        self, db_session, make_user_token, client
    ):
        """PAE-PUB-001: unauthenticated POST returns redirect (require_auth)."""
        author, _ = _new_user(make_user_token)
        prompt = _public_prompt(author)
        _db.session.commit()

        resp = client.post(
            f"/prompts/{prompt.slug}/analytics/explain/trend",
            follow_redirects=False,
        )

        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "/login" in resp.headers.get("Location", "")

    def test_authenticated_post_trend_explanation_completes(
        self, db_session, make_user_token, client
    ):
        """PAE-PUB-002: authenticated POST creates a completed explanation row."""
        author, tok = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)
        _db.session.commit()

        resp = client.post(
            f"/prompts/{prompt.slug}/analytics/explain/trend",
            headers=_auth(tok),
            follow_redirects=False,
        )

        # Route flashes success and redirects to analytics page.
        assert resp.status_code == 302
        assert f"/prompts/{prompt.slug}/analytics" in resp.headers.get("Location", "")

        # Row must be completed (CELERY_TASK_ALWAYS_EAGER=True).
        row = _db.session.scalar(
            _db.session.query(AnalyticsExplanation)
            .filter_by(prompt_post_id=prompt.id, kind="trend")
            .statement
        )
        assert row is not None
        _db.session.refresh(row)
        assert row.status == AnalyticsExplanationStatus.completed.value
        assert row.explanation_md is not None
        assert len(row.explanation_md) > 0

    def test_analytics_page_renders_explanation_section(
        self, db_session, make_user_token, client
    ):
        """PAE-PUB-003: GET analytics page shows AI Analytics Explanations heading."""
        author, tok = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)
        _db.session.commit()

        # Generate an explanation first.
        client.post(
            f"/prompts/{prompt.slug}/analytics/explain/trend",
            headers=_auth(tok),
            follow_redirects=False,
        )

        resp = client.get(
            f"/prompts/{prompt.slug}/analytics",
            headers=_auth(tok),
            follow_redirects=True,
        )

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "AI Analytics Explanations" in body

    def test_invalid_kind_flashes_error_and_redirects(
        self, db_session, make_user_token, client
    ):
        """PAE-PUB-004: invalid kind posts flash an error message."""
        author, tok = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)
        _db.session.commit()

        resp = client.post(
            f"/prompts/{prompt.slug}/analytics/explain/invalid_kind",
            headers=_auth(tok),
            follow_redirects=True,
        )

        # Should flash error and still return 200 after redirect.
        assert resp.status_code == 200
        body = resp.data.decode()
        # The flash message should contain error text indicating invalid kind.
        assert (
            "invalid" in body.lower()
            or "error" in body.lower()
            or "kind" in body.lower()
        )


# ==============================================================================
# PAE-PUB-005 – PAE-PUB-008: Service-layer tests
# ==============================================================================


class TestPublicExplainServiceLayer:
    """Service-level tests for prompt_analytics_explain_service — public scope."""

    def test_unauthenticated_raises_explain_error_401(
        self, db_session, make_user_token
    ):
        """PAE-PUB-005: request_explanation(user=None) raises ExplainError(401)."""
        author, _ = _new_user(make_user_token)
        prompt = _public_prompt(author)
        _db.session.commit()

        with pytest.raises(explain_svc.ExplainError) as exc_info:
            explain_svc.request_explanation(
                user=None, post=prompt, workspace=None, kind="trend"
            )

        assert exc_info.value.status_code == 401

    def test_fork_rationale_explanation_completes(self, db_session, make_user_token):
        """PAE-PUB-006: fork_rationale kind completes with mock provider."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)
        _db.session.commit()

        row = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="fork_rationale"
        )

        _db.session.expire(row)
        assert row.status == AnalyticsExplanationStatus.completed.value
        assert row.explanation_md is not None

    def test_get_explanation_returns_none_before_request(
        self, db_session, make_user_token
    ):
        """PAE-PUB-007: get_explanation returns None when no row exists."""
        author, _ = _new_user(make_user_token)
        prompt = _public_prompt(author)
        _db.session.commit()

        result = explain_svc.get_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )

        assert result is None

    def test_get_explanation_returns_completed_row_after_request(
        self, db_session, make_user_token
    ):
        """PAE-PUB-008: get_explanation returns the completed row."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)
        _db.session.commit()

        row = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )
        _db.session.expire(row)

        result = explain_svc.get_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )

        assert result is not None
        assert result.id == row.id
        assert result.status == AnalyticsExplanationStatus.completed.value

    def test_workspace_post_rejected_via_public_service(
        self, db_session, make_user_token
    ):
        """Service rejects a workspace post accessed via public scope (defence in depth)."""
        from backend.services import workspace_service as ws_svc

        owner, _ = _new_user(make_user_token, role="editor")
        ws = ws_svc.create_workspace(name=f"WS-PUB-{_uid()}", owner=owner)
        _db.session.commit()
        ws_prompt = ws_svc.create_workspace_document(
            workspace=ws,
            author=owner,
            title=f"WS Prompt {_uid()}",
            markdown_body="# WS content.",
        )
        ws_prompt.kind = "prompt"
        _db.session.commit()

        with pytest.raises(explain_svc.ExplainError) as exc_info:
            explain_svc.request_explanation(
                user=owner, post=ws_prompt, workspace=None, kind="trend"
            )

        assert exc_info.value.status_code == 404


# ==============================================================================
# PAE-PUB-009: Feed / sitemap do not expose explanation content
# ==============================================================================


class TestPublicExplainFeedLeakage:
    """Feed and sitemap must not expose analytics explanation data."""

    def test_feed_does_not_contain_explanation_content(
        self, db_session, make_user_token, client
    ):
        """PAE-PUB-009a: /feed.xml does not include explanation markdown."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)
        _db.session.commit()

        # Generate an explanation.
        explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )

        resp = client.get("/feed.xml")
        assert resp.status_code == 200
        feed_body = resp.data.decode()
        # The explanation markdown should not appear in the feed.
        assert "mock" not in feed_body.lower() or "explanation" not in feed_body.lower()
        assert "AI Analytics Explanations" not in feed_body

    def test_sitemap_does_not_contain_explanation_content(
        self, db_session, make_user_token, client
    ):
        """PAE-PUB-009b: /sitemap.xml does not reference explain endpoints."""
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        sitemap_body = resp.data.decode()
        assert "/analytics/explain/" not in sitemap_body
