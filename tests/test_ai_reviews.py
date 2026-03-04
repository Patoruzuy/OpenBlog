"""Tests for the AI Review Engine (v1).

Covers:
  AI-001  Any workspace member can request a review; TASK_ALWAYS_EAGER + mock
          provider → status=completed after request_review() returns.
  AI-002  Non-member gets AIReviewError 404 from the service layer.
  AI-003  Post without a workspace is rejected (workspace-only in v1).
  AI-004  Feature gate: AI_REVIEWS_ENABLED=False → AIReviewError 404.
  AI-005  Invalid review_type → AIReviewError 400.
  AI-006  Dedup: same fingerprint within window returns existing request.
  AI-007  Failed / canceled requests bypass dedup (always retriable).
  AI-008  Rate limit: 11th request in one day raises AIReviewError 429.
  AI-009  Rate limit counter is workspace-scoped (separate workspace = clean).
  AI-010  Requester can cancel a queued review.
  AI-011  Workspace editor can cancel another user's queued review.
  AI-012  Viewer cannot cancel a different user's review (AIReviewError 403).
  AI-013  Completed review cannot be canceled (AIReviewError 400).
  AI-014  Revision review records revision_id; uses diff text as input.
  AI-015  HTTP POST /w/<slug>/docs/<doc>/ai-review → redirect + Cache-Control.
  AI-016  HTTP cancel route → redirect + Cache-Control: private, no-store.
  AI-017  HTTP POST by non-member → 404.
  AI-018  /feed.xml + /sitemap.xml never expose AI review data.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from backend.extensions import db as _db
from backend.models.ai_review import AIReviewRequest, AIReviewResult, AIReviewStatus
from backend.models.post import Post, PostStatus
from backend.models.workspace import WorkspaceMemberRole
from backend.services import ai_review_service as ai_svc
from backend.services import workspace_service as ws_svc
from backend.services.revision_service import RevisionService

# ── module-level counter so helpers produce unique slugs / e-mails ─────────────

_counter = itertools.count(1)


def _uid() -> int:
    return next(_counter)


# ── shared helpers ─────────────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_user(make_user_token, role: str = "reader", *, suffix: str | None = None):
    """Create a user and return (user, token)."""
    n = _uid()
    tag = suffix or str(n)
    return make_user_token(f"ai_user_{tag}@example.com", f"ai_user_{tag}", role=role)


def _workspace_with_owner(make_user_token):
    """Create a workspace and return (workspace, owner, token)."""
    owner, tok = _new_user(make_user_token, role="editor")
    ws = ws_svc.create_workspace(name=f"AI WS {_uid()}", owner=owner)
    _db.session.commit()
    return ws, owner, tok


def _workspace_doc(ws, author) -> Post:
    """Add a workspace document and flush."""
    post = ws_svc.create_workspace_document(
        workspace=ws,
        author=author,
        title=f"Doc {_uid()}",
        markdown_body="# AI Test\n\nThis document is used for AI review tests.",
    )
    _db.session.commit()
    return post


def _public_post(author) -> Post:
    """Create a public (non-workspace) draft and flush."""
    uid = _uid()
    post = Post(
        title=f"Public {uid}",
        slug=f"public-ai-{uid}",
        markdown_body="Public content",
        status=PostStatus.draft,
        author_id=author.id,
        workspace_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _db.session.add(post)
    _db.session.flush()
    return post


# ==============================================================================
# AI-001 – AI-005: Service-layer basics
# ==============================================================================


class TestAIReviewServiceBasics:
    """Request flow, completed state, guard-rails before the task runs."""

    def test_member_review_completes_synchronously(self, db_session, make_user_token):
        """AI-001: with TASK_ALWAYS_EAGER=True the mock provider runs inline."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        req = ai_svc.request_review(owner, post, review_type="clarity")

        # The task ran synchronously in a nested app_context.
        # Re-query to see the committed status.
        _db.session.expire(req)
        assert req.status == AIReviewStatus.completed.value

    def test_completed_review_has_result(self, db_session, make_user_token):
        """AI-001: completed request has an AIReviewResult linked."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        req = ai_svc.request_review(owner, post, review_type="security")

        result = _db.session.get(AIReviewResult, req.id)
        assert result is not None
        assert result.provider == "mock"
        assert result.summary_md is not None
        assert "security" in result.summary_md.lower()
        assert isinstance(result.findings_json, list)
        assert len(result.findings_json) > 0

    def test_non_member_raises_404(self, db_session, make_user_token):
        """AI-002: non-member calling request_review gets AIReviewError(404)."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        outsider, _ = _new_user(make_user_token)

        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.request_review(outsider, post, review_type="full")

        assert exc_info.value.status_code == 404

    def test_public_post_raises_404(self, db_session, make_user_token):
        """AI-003: post with workspace_id=NULL is rejected (workspace-only v1)."""
        owner, _ = _new_user(make_user_token)
        public_post = _public_post(owner)

        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.request_review(owner, public_post, review_type="full")

        assert exc_info.value.status_code == 404

    def test_feature_disabled_raises_404(self, db_session, make_user_token, app):
        """AI-004: AI_REVIEWS_ENABLED=False gates all requests with 404."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        original = app.config.get("AI_REVIEWS_ENABLED", True)
        try:
            app.config["AI_REVIEWS_ENABLED"] = False
            with pytest.raises(ai_svc.AIReviewError) as exc_info:
                ai_svc.request_review(owner, post, review_type="full")
            assert exc_info.value.status_code == 404
        finally:
            app.config["AI_REVIEWS_ENABLED"] = original

    def test_invalid_review_type_raises_400(self, db_session, make_user_token):
        """AI-005: unknown review type is rejected before any DB write."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.request_review(owner, post, review_type="malware_scan")

        assert exc_info.value.status_code == 400

    def test_viewer_member_can_request_review(self, db_session, make_user_token):
        """Any member role (including viewer) may request a review."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        viewer, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        req = ai_svc.request_review(viewer, post, review_type="clarity")

        _db.session.expire(req)
        assert req.status == AIReviewStatus.completed.value
        assert req.requested_by_user_id == viewer.id

    def test_get_latest_reviews_for_post_returns_newest_first(
        self, db_session, make_user_token
    ):
        """get_latest_reviews_for_post returns at most `limit` rows, newest first."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        # Two separate docs so fingerprints differ, avoiding dedup.
        post1 = _workspace_doc(ws, owner)
        post2 = _workspace_doc(ws, owner)

        req1 = ai_svc.request_review(owner, post1, review_type="clarity")
        req2 = ai_svc.request_review(owner, post2, review_type="security")

        reviews = ai_svc.get_latest_reviews_for_post(post1.id, limit=5)
        ids = [r.id for r in reviews]
        assert req1.id in ids
        assert req2.id not in ids  # belongs to post2


# ==============================================================================
# AI-006 – AI-007: Deduplication
# ==============================================================================


class TestAIReviewDedup:
    """Fingerprint-based dedup logic."""

    def test_same_fingerprint_returns_existing_request(
        self, db_session, make_user_token
    ):
        """AI-006: calling request_review twice with identical inputs returns one row."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        req1 = ai_svc.request_review(owner, post, review_type="full")
        req2 = ai_svc.request_review(owner, post, review_type="full")

        assert req1.id == req2.id, "Dedup must return the same request row"

        # Only one row should be present in DB.
        from sqlalchemy import func, select

        count = _db.session.scalar(
            select(func.count()).where(AIReviewRequest.post_id == post.id)
        )
        assert count == 1

    def test_different_review_type_creates_new_request(
        self, db_session, make_user_token
    ):
        """Changing review_type changes the fingerprint → new row."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        req_clarity = ai_svc.request_review(owner, post, review_type="clarity")
        req_security = ai_svc.request_review(owner, post, review_type="security")

        assert req_clarity.id != req_security.id

    def test_failed_request_is_not_deduped(self, db_session, make_user_token):
        """AI-007: a failed request with the same fingerprint allows a new one."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        # Manually insert a failed request with the same fingerprint.
        from backend.services.ai_review_service import (
            _compute_fingerprint,
            _get_input_text,
        )

        input_text = _get_input_text(post, None)
        fp = _compute_fingerprint(
            post_id=post.id,
            revision_id=None,
            review_type="full",
            content_prefix=input_text,
        )
        failed_req = AIReviewRequest(
            workspace_id=ws.id,
            post_id=post.id,
            revision_id=None,
            requested_by_user_id=owner.id,
            review_type="full",
            status=AIReviewStatus.failed.value,
            priority=0,
            input_fingerprint=fp,
            created_at=datetime.now(UTC),
        )
        _db.session.add(failed_req)
        _db.session.commit()

        # New request must create a fresh row.
        new_req = ai_svc.request_review(owner, post, review_type="full")

        assert new_req.id != failed_req.id

    def test_canceled_request_is_not_deduped(
        self, db_session, make_user_token, stub_ai_review_task
    ):
        """AI-007: a canceled request with the same fingerprint allows a fresh one."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        # Request and immediately cancel (task is stubbed → stays queued).
        req = ai_svc.request_review(owner, post, review_type="architecture")
        ai_svc.cancel_review(req.id, owner)

        # Stub no longer needed; task will run eagerly for the new request.
        stub_ai_review_task.reset_mock()

        new_req = ai_svc.request_review(owner, post, review_type="architecture")
        assert new_req.id != req.id


# ==============================================================================
# AI-008 – AI-009: Rate limiting
# ==============================================================================


class TestAIReviewRateLimit:
    """Redis-backed daily rate limit (fakeredis in tests)."""

    def test_daily_limit_enforced(self, db_session, make_user_token):
        """AI-008: the 11th request on the same day raises AIReviewError(429)."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        # Create enough separate posts so fingerprints differ and dedup
        # doesn't short-circuit before the rate-limit counter grows to 10.
        posts = [_workspace_doc(ws, owner) for _ in range(11)]

        for post in posts[:10]:
            ai_svc.request_review(owner, post, review_type="clarity")

        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.request_review(owner, posts[10], review_type="clarity")

        assert exc_info.value.status_code == 429

    def test_rate_limit_is_workspace_scoped(self, db_session, make_user_token):
        """AI-009: exhausting one workspace's quota doesn't affect another."""
        ws_a, owner_a, _ = _workspace_with_owner(make_user_token)
        ws_b, owner_b, _ = _workspace_with_owner(make_user_token)

        # Same user is a member of both workspaces.
        user, _ = _new_user(make_user_token)
        ws_svc.add_member(ws_a, user, WorkspaceMemberRole.contributor)
        ws_svc.add_member(ws_b, user, WorkspaceMemberRole.contributor)
        _db.session.commit()

        # Exhaust quota on workspace A.
        posts_a = [_workspace_doc(ws_a, owner_a) for _ in range(11)]
        for post in posts_a[:10]:
            # Request as the shared user.
            _db.session.refresh(post)
            ai_svc.request_review(user, post, review_type="clarity")

        # Workspace B is unaffected: one more request should succeed.
        post_b = _workspace_doc(ws_b, owner_b)
        req = ai_svc.request_review(user, post_b, review_type="clarity")
        _db.session.expire(req)
        assert req.status == AIReviewStatus.completed.value


# ==============================================================================
# AI-010 – AI-013: Cancel
# ==============================================================================


class TestAIReviewCancel:
    """Cancel-permission matrix."""

    def _queue_review(self, make_user_token, stub_ai_review_task):
        """Return (ws, post, requester, requester_tok, queued_req)."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        requester, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, requester, WorkspaceMemberRole.contributor)
        _db.session.commit()

        # stub_ai_review_task keeps the request in 'queued' state.
        req = ai_svc.request_review(requester, post, review_type="full")
        return ws, post, owner, requester, req

    def test_requester_can_cancel_queued(
        self, db_session, make_user_token, stub_ai_review_task
    ):
        """AI-010: the user who submitted the review can cancel it."""
        _, _, _, requester, req = self._queue_review(
            make_user_token, stub_ai_review_task
        )
        canceled = ai_svc.cancel_review(req.id, requester)
        assert canceled.status == AIReviewStatus.canceled.value

    def test_editor_can_cancel_others_review(
        self, db_session, make_user_token, stub_ai_review_task
    ):
        """AI-011: workspace editor can cancel any member's queued review."""
        ws, post, owner, requester, req = self._queue_review(
            make_user_token, stub_ai_review_task
        )
        # owner has 'owner' role which meets editor+
        canceled = ai_svc.cancel_review(req.id, owner)
        assert canceled.status == AIReviewStatus.canceled.value

    def test_viewer_cannot_cancel_others_review(
        self, db_session, make_user_token, stub_ai_review_task
    ):
        """AI-012: viewer cannot cancel another user's review (403)."""
        ws, post, owner, requester, req = self._queue_review(
            make_user_token, stub_ai_review_task
        )
        viewer, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.cancel_review(req.id, viewer)

        assert exc_info.value.status_code == 403

    def test_cannot_cancel_completed_review(self, db_session, make_user_token):
        """AI-013: canceling a completed review raises AIReviewError(400)."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        # Let the task run synchronously → completed.
        req = ai_svc.request_review(owner, post, review_type="clarity")
        _db.session.expire(req)
        assert req.status == AIReviewStatus.completed.value

        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.cancel_review(req.id, owner)

        assert exc_info.value.status_code == 400

    def test_requester_cannot_cancel_others_workspace_review_as_viewer(
        self, db_session, make_user_token, stub_ai_review_task
    ):
        """Viewer who is not the requester cannot cancel the review."""
        ws, post, owner, requester, req = self._queue_review(
            make_user_token, stub_ai_review_task
        )
        other_viewer, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, other_viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.cancel_review(req.id, other_viewer)

        assert exc_info.value.status_code == 403


# ==============================================================================
# AI-014: Revision diff review
# ==============================================================================


class TestAIReviewRevision:
    """review_type applies to revision diff, not the full post body."""

    def test_revision_review_records_revision_id(self, db_session, make_user_token):
        """AI-014a: request with a revision sets revision_id on the row."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        # RevisionService requires published post.
        post.status = PostStatus.published
        _db.session.commit()
        contributor, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, contributor, WorkspaceMemberRole.contributor)
        _db.session.commit()

        revision = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,
            proposed_markdown="# AI Test\n\nImproved content with clearer structure.",
            summary="Improve clarity",
        )
        _db.session.commit()

        req = ai_svc.request_review(
            contributor, post, revision=revision, review_type="clarity"
        )
        assert req.revision_id == revision.id

    def test_revision_review_uses_diff_as_input(self, db_session, make_user_token):
        """AI-014b: the metrics_json.input_chars reflects the diff, not the full body."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        contrib, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, contrib, WorkspaceMemberRole.contributor)
        # Post has long body; diff will be shorter.
        long_body = "# Heading\n\n" + ("Line of content.\n" * 50)
        post = Post(
            title=f"Long Doc {_uid()}",
            slug=f"long-doc-ai-{_uid()}",
            markdown_body=long_body,
            status=PostStatus.published,  # RevisionService requires published
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.commit()

        # contrib is not the author so RevisionService allows this.
        revision = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown=long_body + "One extra line.\n",
            summary="Append a line",
        )
        _db.session.commit()

        req = ai_svc.request_review(owner, post, revision=revision, review_type="full")

        result = _db.session.get(AIReviewResult, req.id)
        assert result is not None
        diff_input_chars: int = result.metrics_json.get("input_chars", 0)  # type: ignore[union-attr]
        # The diff is shorter than the full body (body is 800+ chars).
        assert diff_input_chars < len(long_body), (
            "Revision review must send the diff, not the full post body"
        )

    def test_body_review_uses_full_markdown(self, db_session, make_user_token):
        """When no revision given, the full post body is sent to the provider."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        body = "# Heading\n\n" + ("Word. " * 100)
        post = Post(
            title=f"Body Doc {_uid()}",
            slug=f"body-doc-ai-{_uid()}",
            markdown_body=body,
            status=PostStatus.draft,
            author_id=owner.id,
            workspace_id=ws.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(post)
        _db.session.commit()

        req = ai_svc.request_review(owner, post, review_type="full")
        result = _db.session.get(AIReviewResult, req.id)
        assert result is not None
        input_chars: int = result.metrics_json.get("input_chars", 0)  # type: ignore[union-attr]
        assert input_chars == len(body)


# ==============================================================================
# AI-015 – AI-017: HTTP routes (SSR)
# ==============================================================================


class TestAIReviewHTTPRoutes:
    """Smoke-test the SSR POST endpoints via the test client."""

    def _setup(self, make_user_token):
        ws, owner, owner_tok = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        return ws, post, owner, owner_tok

    def test_post_doc_review_redirects_for_member(
        self, auth_client, db_session, make_user_token
    ):
        """AI-015: valid POST returns a redirect (302) with Cache-Control."""
        ws, post, owner, owner_tok = self._setup(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review",
            data={"review_type": "clarity"},
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert "ai-review" in resp.headers.get("Location", "")
        assert "no-store" in resp.headers.get("Cache-Control", "")

    def test_post_doc_review_unauthenticated_redirects_to_login(
        self, auth_client, db_session, make_user_token
    ):
        """Unauthenticated request is redirected to the login page."""
        ws, post, owner, _ = self._setup(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review",
            data={"review_type": "full"},
            follow_redirects=False,
        )

        # require_auth redirects to /auth/login
        assert resp.status_code in (302, 401)

    def test_post_doc_review_non_member_gets_404(
        self, auth_client, db_session, make_user_token
    ):
        """AI-017: non-member POST returns 404."""
        ws, post, owner, _ = self._setup(make_user_token)
        outsider, outsider_tok = _new_user(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review",
            data={"review_type": "full"},
            headers=_auth(outsider_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 404

    def test_cancel_route_redirects_with_no_store(
        self, auth_client, db_session, make_user_token, stub_ai_review_task
    ):
        """AI-016: cancel POST follows up with redirect + Cache-Control."""
        ws, post, owner, owner_tok = self._setup(make_user_token)

        # Queue a review (task stubbed → stays queued).
        req = ai_svc.request_review(owner, post, review_type="full")

        resp = auth_client.post(
            f"/w/{ws.slug}/ai-reviews/{req.id}/cancel",
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert "no-store" in resp.headers.get("Cache-Control", "")

        # Verify status was updated in DB.
        _db.session.expire(req)
        assert req.status == AIReviewStatus.canceled.value

    def test_cancel_non_member_gets_404(
        self, auth_client, db_session, make_user_token, stub_ai_review_task
    ):
        """Non-member trying to cancel gets 404 (workspace gate)."""
        ws, post, owner, owner_tok = self._setup(make_user_token)
        req = ai_svc.request_review(owner, post, review_type="full")
        outsider, outsider_tok = _new_user(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/ai-reviews/{req.id}/cancel",
            headers=_auth(outsider_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 404

    def test_revision_review_route_redirects(
        self, auth_client, db_session, make_user_token
    ):
        """POST /w/<slug>/revisions/<id>/ai-review redirects member to doc #ai-review."""
        ws, post, owner, owner_tok = self._setup(make_user_token)
        # RevisionService requires the post to be published.
        post.status = PostStatus.published
        _db.session.commit()

        # Use a contributor (non-author) to submit the revision.
        contrib, contrib_tok = _new_user(make_user_token)
        ws_svc.add_member(ws, contrib, WorkspaceMemberRole.contributor)
        _db.session.commit()

        revision = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown="# AI Test\n\nRevised content here.",
            summary="Minor revision",
        )
        _db.session.commit()

        # The owner (member) requests the review.
        resp = auth_client.post(
            f"/w/{ws.slug}/revisions/{revision.id}/ai-review",
            data={"review_type": "security"},
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert "ai-review" in resp.headers.get("Location", "")

    def test_default_review_type_is_full(
        self, auth_client, db_session, make_user_token
    ):
        """Omitting review_type form field defaults to 'full'."""
        ws, post, owner, owner_tok = self._setup(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review",
            data={},
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 302
        req = ai_svc.get_latest_reviews_for_post(post.id, limit=1)[0]
        assert req.review_type == "full"


# ==============================================================================
# AI-018: Public feed / sitemap isolation
# ==============================================================================


class TestAIReviewFeedIsolation:
    """AI review data must never leak into public feeds or sitemap."""

    def _make_workspace_post_with_review(self, make_user_token):
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        ai_svc.request_review(owner, post, review_type="full")
        return ws, post, owner

    def test_feed_xml_excludes_ai_review_data(
        self, auth_client, db_session, make_user_token
    ):
        """AI-018a: /feed.xml does not contain ai_review / summary_md tokens."""
        self._make_workspace_post_with_review(make_user_token)

        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        body = resp.data.decode()

        assert "ai_review" not in body.lower()
        assert "Mock" not in body  # mock summary_md starts with **Mock …**

    def test_feed_json_excludes_ai_review_data(
        self, auth_client, db_session, make_user_token
    ):
        """AI-018b: /feed.json does not contain ai_review tokens."""
        self._make_workspace_post_with_review(make_user_token)

        resp = auth_client.get("/feed.json")
        assert resp.status_code == 200
        body = resp.data.decode()

        assert "ai_review" not in body.lower()

    def test_sitemap_excludes_ai_review_urls(
        self, auth_client, db_session, make_user_token
    ):
        """AI-018c: /sitemap.xml does not include any /ai-review paths."""
        self._make_workspace_post_with_review(make_user_token)

        resp = auth_client.get("/sitemap.xml")
        assert resp.status_code == 200
        body = resp.data.decode()

        assert "ai-review" not in body


# ==============================================================================
# get_review / get_latest_reviews_for_post permission contract
# ==============================================================================


class TestAIReviewGetAccess:
    """Read-path permission enforcement."""

    def test_get_review_non_member_raises_404(self, db_session, make_user_token):
        """A non-member loading a review by ID gets AIReviewError(404)."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = ai_svc.request_review(owner, post, review_type="full")

        outsider, _ = _new_user(make_user_token)
        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.get_review(req.id, outsider)

        assert exc_info.value.status_code == 404

    def test_get_review_member_succeeds(self, db_session, make_user_token):
        """Any workspace member can load a review by ID."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = ai_svc.request_review(owner, post, review_type="clarity")

        viewer, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        loaded = ai_svc.get_review(req.id, viewer)
        assert loaded.id == req.id

    def test_get_review_missing_id_raises_404(self, db_session, make_user_token):
        """Requesting a non-existent request_id raises AIReviewError(404)."""
        user, _ = _new_user(make_user_token)
        with pytest.raises(ai_svc.AIReviewError) as exc_info:
            ai_svc.get_review(999_999, user)
        assert exc_info.value.status_code == 404
