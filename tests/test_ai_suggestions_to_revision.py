"""Tests for the AI Suggestion-to-Revision workflow.

Covers:
  AIR-001  Contributor can create a revision from a clarity suggestion
           (replace_block edit applied correctly).
  AIR-002  Owner (post author) can also create a revision from a suggestion.
  AIR-003  Viewer role cannot create a revision (AIRevisionError 403).
  AIR-004  Non-member gets AIRevisionError 404 from the service.
  AIR-005  Unknown suggestion_id raises AIRevisionError 400.
  AIR-006  Target text absent in document raises AIRevisionError 400.
  AIR-007  Created revision carries correct source_metadata_json provenance.
  AIR-008  Created revision is linked to the correct post (post_id).
  AIR-009  Review not yet completed → AIRevisionError 400.
  AIR-010  Append-block suggestion (architecture) always succeeds.
  AIR-011  Insert-after-heading suggestion (security) inserts at correct position.
  AIR-012  Feed XML is unaffected by suggestion/revision data.
  AIR-013  HTTP route: POST by contributor → 302 redirect to #revisions.
  AIR-014  HTTP route: viewer POST → 403.
  AIR-015  _apply_edit unit — replace_block succeeds.
  AIR-016  _apply_edit unit — append_block always succeeds.
  AIR-017  _apply_edit unit — insert_after_heading inserts after correct line.
  AIR-018  _apply_edit unit — replace_block target not found raises 400.
  AIR-019  _apply_edit unit — insert_after_heading no match raises 400.
  AIR-020  _apply_edit unit — unknown kind raises 400.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.ai_review import AIReviewRequest, AIReviewStatus
from backend.models.post import Post
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import WorkspaceMemberRole
from backend.services import ai_review_service as ai_svc
from backend.services import ai_revision_service as ai_rev_svc
from backend.services import workspace_service as ws_svc

# ── module-level counter for unique slugs / e-mails ───────────────────────────

_counter = itertools.count(1_000)


def _uid() -> int:
    return next(_counter)


# ── test helpers ───────────────────────────────────────────────────────────────

# The canonical fixture body that mock provider edit targets are written against.
FIXTURE_BODY = "# AI Test\n\nThis document is used for AI review tests."


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_user(make_user_token, role: str = "reader", *, suffix: str | None = None):
    n = _uid()
    tag = suffix or str(n)
    return make_user_token(f"air_user_{tag}@example.com", f"air_user_{tag}", role=role)


def _workspace_with_owner(make_user_token):
    owner, tok = _new_user(make_user_token, role="editor")
    ws = ws_svc.create_workspace(name=f"AIR WS {_uid()}", owner=owner)
    _db.session.commit()
    return ws, owner, tok


def _workspace_doc(ws, author, body: str = FIXTURE_BODY) -> Post:
    post = ws_svc.create_workspace_document(
        workspace=ws,
        author=author,
        title=f"AIR Doc {_uid()}",
        markdown_body=body,
    )
    _db.session.commit()
    return post


def _completed_review(owner, post, review_type: str = "clarity") -> AIReviewRequest:
    """Request an AI review and return the completed request (CELERY_TASK_ALWAYS_EAGER)."""
    req = ai_svc.request_review(owner, post, review_type=review_type)
    _db.session.expire(req)
    assert req.status == AIReviewStatus.completed.value, (
        f"Review did not complete synchronously; status={req.status!r}"
    )
    return req


# ==============================================================================
# AIR-001 – AIR-011: Service-layer tests
# ==============================================================================


class TestAIRevisionServiceBasics:
    """Core service behaviour: edit types, permissions, guard-rails."""

    def test_contributor_creates_revision_from_clarity_suggestion(
        self, db_session, make_user_token
    ):
        """AIR-001: contributor can create a pending revision via replace_block."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="clarity")

        contrib, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, contrib, WorkspaceMemberRole.contributor)
        _db.session.commit()

        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=contrib,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="clarity-1",
        )

        assert revision is not None
        assert revision.status == RevisionStatus.pending

        # The proposed markdown must contain the replacement text.
        from backend.ai.providers.mock import MOCK_REPLACE_PROPOSED  # noqa: PLC0415

        assert MOCK_REPLACE_PROPOSED in revision.proposed_markdown

    def test_owner_creates_revision_from_suggestion(
        self, db_session, make_user_token
    ):
        """AIR-002: owner (post author) is not blocked from AI-sourced revisions."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="architecture")

        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="arch-1",
        )

        assert revision.status == RevisionStatus.pending
        assert revision.author_id == owner.id

    def test_viewer_cannot_create_revision(self, db_session, make_user_token):
        """AIR-003: a viewer-role member gets AIRevisionError 403."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="clarity")

        viewer, _ = _new_user(make_user_token)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc.create_revision_from_ai_suggestion(
                user=viewer,
                post=post,
                ai_review_request_id=req.id,
                suggestion_id="clarity-1",
            )

        assert exc_info.value.status_code == 403

    def test_non_member_gets_404(self, db_session, make_user_token):
        """AIR-004: caller with no workspace membership gets AIRevisionError 404."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="clarity")

        outsider, _ = _new_user(make_user_token)

        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc.create_revision_from_ai_suggestion(
                user=outsider,
                post=post,
                ai_review_request_id=req.id,
                suggestion_id="clarity-1",
            )

        assert exc_info.value.status_code == 404

    def test_invalid_suggestion_id_raises_400(self, db_session, make_user_token):
        """AIR-005: unknown suggestion_id raises AIRevisionError 400."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="clarity")

        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc.create_revision_from_ai_suggestion(
                user=owner,
                post=post,
                ai_review_request_id=req.id,
                suggestion_id="nonexistent-99",
            )

        assert exc_info.value.status_code == 400
        assert "nonexistent-99" in exc_info.value.message

    def test_target_not_found_raises_400(self, db_session, make_user_token):
        """AIR-006: replace_block edit with absent target raises AIRevisionError 400."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        # Use a body that does NOT contain MOCK_REPLACE_TARGET.
        post = _workspace_doc(
            ws, owner, body="# AI Test\n\nCompletely different content here."
        )
        req = _completed_review(owner, post, review_type="clarity")

        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc.create_revision_from_ai_suggestion(
                user=owner,
                post=post,
                ai_review_request_id=req.id,
                suggestion_id="clarity-1",
            )

        assert exc_info.value.status_code == 400
        assert "not found" in exc_info.value.message.lower()

    def test_created_revision_has_source_metadata(
        self, db_session, make_user_token
    ):
        """AIR-007: source_metadata_json attributes the AI origin correctly."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="clarity")

        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="clarity-1",
        )

        meta = revision.source_metadata_json
        assert meta is not None
        assert meta["source"] == "ai_suggestion"
        assert meta["ai_review_request_id"] == req.id
        assert meta["suggestion_id"] == "clarity-1"

    def test_created_revision_linked_to_post(self, db_session, make_user_token):
        """AIR-008: created revision has correct post_id and author_id."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="architecture")

        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="arch-1",
        )

        # Reload from DB to confirm persistence.
        _db.session.expire(revision)
        loaded = _db.session.get(Revision, revision.id)
        assert loaded is not None
        assert loaded.post_id == post.id
        assert loaded.author_id == owner.id

    def test_incomplete_review_raises_400(self, db_session, make_user_token, stub_ai_review_task):
        """AIR-009: a queued (not completed) review cannot spawn a revision."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)

        # Task is stubbed → review stays queued.
        req = ai_svc.request_review(owner, post, review_type="clarity")
        assert req.status == AIReviewStatus.queued.value

        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc.create_revision_from_ai_suggestion(
                user=owner,
                post=post,
                ai_review_request_id=req.id,
                suggestion_id="clarity-1",
            )

        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.message.lower()

    def test_append_block_suggestion_succeeds(self, db_session, make_user_token):
        """AIR-010: architecture suggestion (append_block) always succeeds."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="architecture")

        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="arch-1",
        )

        assert revision.status == RevisionStatus.pending
        from backend.ai.providers.mock import MOCK_APPEND_BLOCK  # noqa: PLC0415

        # The appended block should appear at the end of the proposed markdown.
        assert MOCK_APPEND_BLOCK.strip() in revision.proposed_markdown

    def test_insert_after_heading_suggestion(self, db_session, make_user_token):
        """AIR-011: security suggestion (insert_after_heading) inserts after heading."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)  # body has "# AI Test" heading
        req = _completed_review(owner, post, review_type="security")

        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="security-1",
        )

        from backend.ai.providers.mock import MOCK_HEADING_INSERT  # noqa: PLC0415

        proposed = revision.proposed_markdown
        # The inserted block must appear after the "# AI Test" heading line.
        heading_pos = proposed.find("# AI Test")
        insert_pos = proposed.find(MOCK_HEADING_INSERT.strip())
        assert heading_pos != -1, "Heading should still be present"
        assert insert_pos != -1, "Inserted block not found in proposed markdown"
        assert insert_pos > heading_pos, "Inserted block must appear after the heading"


# ==============================================================================
# AIR-012: Feed isolation
# ==============================================================================


class TestAISuggestionFeedIsolation:
    """Revision and suggestion data must not leak into public feeds."""

    def test_feed_xml_unaffected_by_ai_revision(
        self, auth_client, db_session, make_user_token
    ):
        """AIR-012: /feed.xml does not expose revision or ai_suggestion tokens."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="architecture")
        ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="arch-1",
        )

        resp = auth_client.get("/feed.xml")
        assert resp.status_code == 200
        body = resp.data.decode()

        assert "ai_suggestion" not in body
        assert "source_metadata" not in body


# ==============================================================================
# AIR-013 – AIR-014: HTTP route tests
# ==============================================================================


class TestAISuggestionHTTPRoutes:
    """HTTP-level contracts for the create-revision route."""

    def _setup(self, make_user_token):
        ws, owner, owner_tok = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="architecture")
        return ws, post, owner, owner_tok, req

    def test_contributor_create_revision_redirects_to_revisions(
        self, auth_client, db_session, make_user_token
    ):
        """AIR-013: contributor POST → 302 redirecting to #revisions anchor."""
        ws, post, owner, _, req = self._setup(make_user_token)

        contrib, contrib_tok = _new_user(make_user_token)
        ws_svc.add_member(ws, contrib, WorkspaceMemberRole.contributor)
        _db.session.commit()

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review/{req.id}"
            f"/suggestions/arch-1/create-revision",
            headers=_auth(contrib_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "revisions" in location

    def test_viewer_role_gets_403_on_route(
        self, auth_client, db_session, make_user_token
    ):
        """AIR-014: viewer POST → 403 Forbidden."""
        ws, post, owner, _, req = self._setup(make_user_token)

        viewer, viewer_tok = _new_user(make_user_token)
        ws_svc.add_member(ws, viewer, WorkspaceMemberRole.viewer)
        _db.session.commit()

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review/{req.id}"
            f"/suggestions/arch-1/create-revision",
            headers=_auth(viewer_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 403

    def test_non_member_gets_404_on_route(
        self, auth_client, db_session, make_user_token
    ):
        """Non-member POST → 404 (workspace gate)."""
        ws, post, owner, _, req = self._setup(make_user_token)
        outsider, outsider_tok = _new_user(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review/{req.id}"
            f"/suggestions/arch-1/create-revision",
            headers=_auth(outsider_tok),
            follow_redirects=False,
        )

        assert resp.status_code == 404

    def test_bad_suggestion_id_flashes_error_and_redirects(
        self, auth_client, db_session, make_user_token
    ):
        """Bad suggestion_id → flash error + redirect back (302), not 500."""
        ws, post, owner, owner_tok, req = self._setup(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review/{req.id}"
            f"/suggestions/totally-bogus/create-revision",
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        # Service raises 400 → route flashes and redirects.
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "ai-review" in location

    def test_cache_control_header_present(
        self, auth_client, db_session, make_user_token
    ):
        """Blueprint after_request hook sets Cache-Control: private, no-store."""
        ws, post, owner, owner_tok, req = self._setup(make_user_token)

        resp = auth_client.post(
            f"/w/{ws.slug}/docs/{post.slug}/ai-review/{req.id}"
            f"/suggestions/arch-1/create-revision",
            headers=_auth(owner_tok),
            follow_redirects=False,
        )

        assert "no-store" in resp.headers.get("Cache-Control", "")


# ==============================================================================
# AIR-015 – AIR-020: _apply_edit unit tests
# ==============================================================================


class TestApplyEditUnit:
    """Direct unit tests for the ``_apply_edit`` helper (no DB involved)."""

    _BODY = FIXTURE_BODY

    def test_replace_block_succeeds(self):
        """AIR-015: replace_block substitutes the first matching substring."""
        from backend.ai.providers.mock import (  # noqa: PLC0415
            MOCK_REPLACE_PROPOSED,
            MOCK_REPLACE_TARGET,
        )

        edit = {
            "kind": "replace_block",
            "target_hint": {"match": MOCK_REPLACE_TARGET},
            "proposed_markdown": MOCK_REPLACE_PROPOSED,
        }
        result = ai_rev_svc._apply_edit(self._BODY, edit)

        assert MOCK_REPLACE_TARGET not in result
        assert MOCK_REPLACE_PROPOSED in result

    def test_append_block_succeeds(self):
        """AIR-016: append_block adds content after the last line."""
        edit = {
            "kind": "append_block",
            "target_hint": {},
            "proposed_markdown": "## New Section\n\nAdded content.",
        }
        result = ai_rev_svc._apply_edit(self._BODY, edit)

        assert result.startswith(self._BODY.rstrip("\n"))
        assert "## New Section" in result
        assert "Added content." in result

    def test_insert_after_heading_succeeds(self):
        """AIR-017: insert_after_heading places content on the line after the heading."""
        from backend.ai.providers.mock import (  # noqa: PLC0415
            MOCK_HEADING_INSERT,
            MOCK_HEADING_TARGET,
        )

        edit = {
            "kind": "insert_after_heading",
            "target_hint": {"heading": MOCK_HEADING_TARGET},
            "proposed_markdown": MOCK_HEADING_INSERT,
        }
        result = ai_rev_svc._apply_edit(self._BODY, edit)

        lines = result.splitlines()
        heading_idx = next(
            i for i, ln in enumerate(lines) if ln.lstrip("#").strip() == MOCK_HEADING_TARGET
        )
        # The inserted content should appear somewhere after the heading line.
        tail = "\n".join(lines[heading_idx + 1 :])
        assert MOCK_HEADING_INSERT.strip() in tail

    def test_replace_block_target_not_found_raises_400(self):
        """AIR-018: replace_block with absent target raises AIRevisionError(400)."""
        edit = {
            "kind": "replace_block",
            "target_hint": {"match": "this text is definitely not in the body"},
            "proposed_markdown": "replacement",
        }
        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc._apply_edit(self._BODY, edit)

        assert exc_info.value.status_code == 400
        assert "not found" in exc_info.value.message.lower()

    def test_insert_after_heading_not_found_raises_400(self):
        """AIR-019: insert_after_heading with absent heading raises AIRevisionError(400)."""
        edit = {
            "kind": "insert_after_heading",
            "target_hint": {"heading": "Heading That Does Not Exist"},
            "proposed_markdown": "some content",
        }
        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc._apply_edit(self._BODY, edit)

        assert exc_info.value.status_code == 400
        assert "heading not found" in exc_info.value.message.lower()

    def test_unknown_kind_raises_400(self):
        """AIR-020: an unrecognised edit kind raises AIRevisionError(400)."""
        edit = {
            "kind": "teleport_block",
            "target_hint": {},
            "proposed_markdown": "content",
        }
        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc._apply_edit(self._BODY, edit)

        assert exc_info.value.status_code == 400
        assert "teleport_block" in exc_info.value.message

    def test_replace_block_missing_match_key_raises_400(self):
        """Empty target_hint.match for replace_block raises 400 immediately."""
        edit = {
            "kind": "replace_block",
            "target_hint": {},          # no "match" key
            "proposed_markdown": "x",
        }
        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc._apply_edit(self._BODY, edit)

        assert exc_info.value.status_code == 400

    def test_insert_after_heading_missing_heading_key_raises_400(self):
        """Empty target_hint.heading for insert_after_heading raises 400."""
        edit = {
            "kind": "insert_after_heading",
            "target_hint": {},          # no "heading" key
            "proposed_markdown": "x",
        }
        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc._apply_edit(self._BODY, edit)

        assert exc_info.value.status_code == 400

    def test_replace_block_only_replaces_first_occurrence(self):
        """replace_block replaces only the *first* match, leaving subsequent ones intact."""
        body = "hello world\nhello world\n"
        edit = {
            "kind": "replace_block",
            "target_hint": {"match": "hello world"},
            "proposed_markdown": "goodbye world",
        }
        result = ai_rev_svc._apply_edit(body, edit)

        assert result.count("goodbye world") == 1
        assert result.count("hello world") == 1  # second occurrence still present

    def test_append_block_no_double_blank_line_at_end(self):
        """append_block ensures exactly one separator blank line between body and block."""
        body_with_trailing_newlines = self._BODY + "\n\n\n"
        edit = {
            "kind": "append_block",
            "target_hint": {},
            "proposed_markdown": "appended",
        }
        result = ai_rev_svc._apply_edit(body_with_trailing_newlines, edit)

        # Should not have more than two consecutive newlines.
        assert "\n\n\n" not in result


# ==============================================================================
# AIR  edge cases: review belongs to different workspace/post
# ==============================================================================


class TestAIRevisionCrossWorkspaceSafety:
    """Service must not let a review from workspace A be used for a post in workspace B."""

    def test_review_for_different_post_raises_404(
        self, db_session, make_user_token
    ):
        """Using a review_id that belongs to a different post raises AIRevisionError 404."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post_a = _workspace_doc(ws, owner)
        post_b = _workspace_doc(ws, owner)  # different post, same workspace

        req = _completed_review(owner, post_a, review_type="clarity")

        # Try to use post_a's review for post_b.
        with pytest.raises(ai_rev_svc.AIRevisionError) as exc_info:
            ai_rev_svc.create_revision_from_ai_suggestion(
                user=owner,
                post=post_b,
                ai_review_request_id=req.id,
                suggestion_id="clarity-1",
            )

        assert exc_info.value.status_code == 404

    def test_pending_revision_status_is_always_pending(
        self, db_session, make_user_token
    ):
        """Regardless of the workspace role, created revision is always 'pending'."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="architecture")

        # Even the owner (highest role) creates a pending revision; never auto-accepted.
        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="arch-1",
        )

        assert revision.status == RevisionStatus.pending, (
            "AI suggestions must NEVER auto-accept; human-in-the-loop is mandatory."
        )

    def test_revision_summary_contains_ai_attribution(
        self, db_session, make_user_token
    ):
        """Revision summary line includes AI suggestion prefix and review id."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        post = _workspace_doc(ws, owner)
        req = _completed_review(owner, post, review_type="clarity")

        revision = ai_rev_svc.create_revision_from_ai_suggestion(
            user=owner,
            post=post,
            ai_review_request_id=req.id,
            suggestion_id="clarity-1",
        )

        assert "[AI suggestion]" in revision.summary
        assert str(req.id) in revision.summary
        assert "clarity-1" in revision.summary
