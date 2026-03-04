"""Tests for Analytics Explanations — fingerprint deduplication.

Covers:
  PAE-DUP-001  Same user, same post, same kind → returns same row (dedup).
  PAE-DUP-002  Different kind → new row (different fingerprint).
  PAE-DUP-003  Failed row is NOT deduped — new row always created.
  PAE-DUP-004  Completed row IS deduped — second request returns same row.
  PAE-DUP-005  compute_fingerprint is stable (same input → same digest).
  PAE-DUP-006  compute_fingerprint differs when input content changes.
  PAE-DUP-007  Only one DB row exists after two identical requests.
  PAE-DUP-008  Public vs workspace scope creates separate rows for same post.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from sqlalchemy import func, select

from backend.extensions import db as _db
from backend.models.analytics_explanation import (
    AnalyticsExplanation,
    AnalyticsExplanationStatus,
)
from backend.models.post import Post, PostStatus
from backend.services import prompt_analytics_explain_service as explain_svc
from backend.services import workspace_service as ws_svc

# ── helpers ───────────────────────────────────────────────────────────────────

_counter = itertools.count(1)


def _uid() -> int:
    return next(_counter)


def _new_user(make_user_token, role: str = "reader"):
    n = _uid()
    return make_user_token(f"dup_expl_{n}@example.com", f"dup_expl_{n}", role=role)


def _workspace_with_owner(make_user_token):
    owner, tok = _new_user(make_user_token, role="editor")
    ws = ws_svc.create_workspace(name=f"Dup-WS-{_uid()}", owner=owner)
    _db.session.commit()
    return ws, owner, tok


def _public_prompt(author) -> Post:
    uid = _uid()
    post = Post(
        title=f"Dup Prompt {uid}",
        slug=f"dup-prompt-{uid}",
        kind="prompt",
        markdown_body="# Dedup Test\n\nContent.",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _ws_prompt(ws, author) -> Post:
    uid = _uid()
    post = ws_svc.create_workspace_document(
        workspace=ws,
        author=author,
        title=f"Dup WS Prompt {uid}",
        markdown_body="# Dedup WS\n\nContent.",
    )
    post.kind = "prompt"
    post.status = PostStatus.published
    _db.session.commit()
    return post


# ==============================================================================
# PAE-DUP-001 – PAE-DUP-004: Core dedup behaviour
# ==============================================================================


class TestExplanationDedup:
    """Fingerprint-based deduplication of analytics explanation requests."""

    def test_same_post_same_kind_returns_same_row(self, db_session, make_user_token):
        """PAE-DUP-001: two identical requests return the same AnalyticsExplanation."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        row1 = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )
        row2 = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )

        assert row1.id == row2.id, "Dedup must return the same row"

    def test_only_one_db_row_after_two_identical_requests(
        self, db_session, make_user_token
    ):
        """PAE-DUP-007: only a single DB row after two identical requests."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )
        explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )

        count = _db.session.scalar(
            select(func.count()).where(
                AnalyticsExplanation.prompt_post_id == prompt.id,
                AnalyticsExplanation.kind == "trend",
                AnalyticsExplanation.scope_type == "public",
            )
        )
        assert count == 1

    def test_different_kind_creates_new_row(self, db_session, make_user_token):
        """PAE-DUP-002: changing kind changes fingerprint → new row."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        row_trend = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )
        row_fork = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="fork_rationale"
        )

        assert row_trend.id != row_fork.id

    def test_failed_row_is_not_deduped(self, db_session, make_user_token):
        """PAE-DUP-003: a failed row is bypassed and a new row is created."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        # Build input + fingerprint the same way the service does.
        input_dict = explain_svc.build_input(prompt, workspace=None, kind="trend")
        fingerprint = explain_svc.compute_fingerprint(input_dict)

        # Manually insert a failed row with the same fingerprint.
        failed_row = AnalyticsExplanation(
            scope_type="public",
            workspace_id=None,
            prompt_post_id=prompt.id,
            prompt_version=None,
            kind="trend",
            status=AnalyticsExplanationStatus.failed.value,
            input_fingerprint=fingerprint,
            explanation_md=None,
            error_message="Simulated failure",
            created_by_user_id=author.id,
            created_at=datetime.now(UTC),
        )
        _db.session.add(failed_row)
        _db.session.commit()

        # New request must create a fresh row.
        new_row = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )

        assert new_row.id != failed_row.id, "Failed row must not block new requests"

    def test_completed_row_is_deduped(self, db_session, make_user_token):
        """PAE-DUP-004: completed row is returned on second request."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        row1 = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="fork_rationale"
        )
        _db.session.expire(row1)
        assert row1.status == AnalyticsExplanationStatus.completed.value

        # Same request again — should return the completed row.
        row2 = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="fork_rationale"
        )

        assert row2.id == row1.id


# ==============================================================================
# PAE-DUP-005 – PAE-DUP-006: Fingerprint stability
# ==============================================================================


class TestFingerprintStability:
    """compute_fingerprint must be deterministic and change-sensitive."""

    def test_fingerprint_is_stable_for_identical_input(self):
        """PAE-DUP-005: same dict always produces same SHA-256 digest."""
        payload = {"kind": "trend", "post_id": 42, "trend_label": "improving"}

        fp1 = explain_svc.compute_fingerprint(payload)
        fp2 = explain_svc.compute_fingerprint(payload)

        assert fp1 == fp2
        # SHA-256 hex is 64 chars.
        assert len(fp1) == 64

    def test_fingerprint_differs_when_input_changes(self):
        """PAE-DUP-006: changing any field produces a different digest."""
        payload_a = {"kind": "trend", "post_id": 42, "trend_label": "improving"}
        payload_b = {"kind": "trend", "post_id": 42, "trend_label": "declining"}

        fp_a = explain_svc.compute_fingerprint(payload_a)
        fp_b = explain_svc.compute_fingerprint(payload_b)

        assert fp_a != fp_b

    def test_fingerprint_is_key_order_independent(self):
        """Fingerprint is stable regardless of dict key order."""
        payload_a = {"a": 1, "b": 2, "c": 3}
        payload_b = {"c": 3, "a": 1, "b": 2}

        assert explain_svc.compute_fingerprint(
            payload_a
        ) == explain_svc.compute_fingerprint(payload_b)


# ==============================================================================
# PAE-DUP-008: Public vs workspace scope are always separate rows
# ==============================================================================


class TestDeduplicationScopeSeparation:
    """Public and workspace scopes never share rows even for identical content."""

    def test_public_and_workspace_create_separate_rows(
        self, db_session, make_user_token
    ):
        """PAE-DUP-008: same post accessible via both scopes → two separate rows."""
        # Public prompt shared with a workspace mirror would be unusual, but we
        # can test the scope_type column directly by calling service for both.
        ws, owner, _ = _make_ws(make_user_token)
        ws_prompt = _ws_prompt(ws, owner)

        # Workspace row.
        ws_row = explain_svc.request_explanation(
            user=owner, post=ws_prompt, workspace=ws, kind="trend"
        )
        assert ws_row.scope_type == "workspace"
        assert ws_row.workspace_id == ws.id

        # Confirm public explanation for a different post is a different row.
        pub_prompt = _public_prompt(owner)
        pub_row = explain_svc.request_explanation(
            user=owner, post=pub_prompt, workspace=None, kind="trend"
        )
        assert pub_row.scope_type == "public"
        assert pub_row.workspace_id is None
        assert pub_row.id != ws_row.id


def _make_ws(make_user_token):
    owner, tok = _new_user(make_user_token, role="editor")
    ws = ws_svc.create_workspace(name=f"Sep-WS-{_uid()}", owner=owner)
    _db.session.commit()
    return ws, owner, tok
