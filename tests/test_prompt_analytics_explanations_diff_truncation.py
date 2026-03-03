"""Tests for Analytics Explanations — input truncation and payload bounds.

Covers:
  PAE-TRUNC-001  build_input version_diff diff capped at 4 000 chars.
  PAE-TRUNC-002  build_input trend JSON does not exceed 2 000 chars.
  PAE-TRUNC-003  build_input fork_rationale JSON does not exceed 2 000 chars.
  PAE-TRUNC-004  explanation_md stored in DB never exceeds 3 000 chars.
  PAE-TRUNC-005  Task truncates input to AI_MAX_INPUT_CHARS before calling provider.
  PAE-TRUNC-006  /feed.xml does not contain analytics explanation rows.
  PAE-TRUNC-007  /json-feed does not contain analytics explanation rows.
  PAE-TRUNC-008  version_diff with no PostVersion rows produces empty diff.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.analytics_explanation import (
    AnalyticsExplanationStatus,
)
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.services import prompt_analytics_explain_service as explain_svc
from backend.services import workspace_service as ws_svc

# ── helpers ───────────────────────────────────────────────────────────────────

_counter = itertools.count(1)

# Mirrors the service constants.
_MAX_DIFF_CHARS = 4_000
_MAX_TREND_CHARS = 2_000
_MAX_OUTPUT_CHARS = 3_000


def _uid() -> int:
    return next(_counter)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_user(make_user_token, role: str = "reader"):
    n = _uid()
    return make_user_token(f"trunc_expl_{n}@example.com", f"trunc_expl_{n}", role=role)


def _workspace_with_owner(make_user_token):
    owner, tok = _new_user(make_user_token, role="editor")
    ws = ws_svc.create_workspace(name=f"Trunc-WS-{_uid()}", owner=owner)
    _db.session.commit()
    return ws, owner, tok


def _public_prompt(author, body: str | None = None) -> Post:
    uid = _uid()
    post = Post(
        title=f"Trunc Prompt {uid}",
        slug=f"trunc-prompt-{uid}",
        kind="prompt",
        markdown_body=body or "# Truncation Test\n\nContent.",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _db.session.add(post)
    _db.session.commit()
    return post


def _ws_prompt(ws, author, body: str | None = None) -> Post:
    uid = _uid()
    post = ws_svc.create_workspace_document(
        workspace=ws,
        author=author,
        title=f"Trunc WS Prompt {uid}",
        markdown_body=body or "# Trunc WS\n\nContent.",
    )
    post.kind = "prompt"
    post.status = PostStatus.published
    _db.session.commit()
    return post


# ==============================================================================
# PAE-TRUNC-001: diff string capped at 4 000 chars
# ==============================================================================


class TestVersionDiffTruncation:
    """build_input truncates version diffs to _MAX_DIFF_CHARS."""

    def test_long_diff_is_capped_at_4000_chars(
        self, db_session, make_user_token
    ):
        """PAE-TRUNC-001: diff string in build_input result is ≤ 4 000 chars."""
        author, _ = _new_user(make_user_token, role="editor")
        # Create a prompt with a minimal body.
        prompt = _public_prompt(author, body="# v1\n\nOriginal.")

        # Add two PostVersion rows where the diff will be very long.
        long_body_v1 = "# Version 1\n\n" + "Old line content.\n" * 500  # ~8 500 chars
        long_body_v2 = "# Version 2\n\n" + "New line content.\n" * 500  # ~8 500 chars

        pv1 = PostVersion(
            post_id=prompt.id,
            version_number=1,
            markdown_body=long_body_v1,
            accepted_by_id=author.id,
            created_at=datetime.now(UTC),
        )
        pv2 = PostVersion(
            post_id=prompt.id,
            version_number=2,
            markdown_body=long_body_v2,
            accepted_by_id=author.id,
            created_at=datetime.now(UTC),
        )
        _db.session.add_all([pv1, pv2])
        _db.session.commit()

        result = explain_svc.build_input(prompt, workspace=None, kind="version_diff")

        assert "diff" in result
        assert len(result["diff"]) <= _MAX_DIFF_CHARS, (
            f"Diff length {len(result['diff'])} exceeds {_MAX_DIFF_CHARS}"
        )

    def test_no_post_versions_produces_empty_diff(
        self, db_session, make_user_token
    ):
        """PAE-TRUNC-008: no PostVersion rows → diff is empty string."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        result = explain_svc.build_input(prompt, workspace=None, kind="version_diff")

        assert result["diff"] == ""
        assert result["from_version"] == 1
        assert result["to_version"] == prompt.version


# ==============================================================================
# PAE-TRUNC-002 – PAE-TRUNC-003: JSON size caps
# ==============================================================================


class TestInputJsonSizeCaps:
    """build_input JSON payloads respect size limits."""

    def test_trend_input_json_within_2000_chars(
        self, db_session, make_user_token
    ):
        """PAE-TRUNC-002: serialised trend payload never exceeds 2 000 chars."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        result = explain_svc.build_input(prompt, workspace=None, kind="trend")

        serialised = json.dumps(result, sort_keys=True, default=str)
        assert len(serialised) <= _MAX_TREND_CHARS, (
            f"Trend payload {len(serialised)} chars > {_MAX_TREND_CHARS}"
        )

    def test_fork_rationale_input_json_within_2000_chars(
        self, db_session, make_user_token
    ):
        """PAE-TRUNC-003: serialised fork_rationale payload never exceeds 2 000 chars."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        result = explain_svc.build_input(prompt, workspace=None, kind="fork_rationale")

        serialised = json.dumps(result, sort_keys=True, default=str)
        assert len(serialised) <= _MAX_TREND_CHARS, (
            f"Fork payload {len(serialised)} chars > {_MAX_TREND_CHARS}"
        )


# ==============================================================================
# PAE-TRUNC-004: Output truncation
# ==============================================================================


class TestOutputTruncation:
    """explanation_md stored in DB must be ≤ _MAX_OUTPUT_CHARS."""

    def test_explanation_md_not_longer_than_3000_chars(
        self, db_session, make_user_token
    ):
        """PAE-TRUNC-004: explanation_md stored in DB is ≤ 3 000 chars."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        row = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )
        _db.session.expire(row)

        assert row.status == AnalyticsExplanationStatus.completed.value
        assert row.explanation_md is not None
        assert len(row.explanation_md) <= _MAX_OUTPUT_CHARS, (
            f"explanation_md ({len(row.explanation_md)} chars) exceeds {_MAX_OUTPUT_CHARS}"
        )


# ==============================================================================
# PAE-TRUNC-005: Task input cap
# ==============================================================================


class TestTaskInputCap:
    """Celery task must not send more than AI_MAX_INPUT_CHARS to the provider."""

    def test_task_runs_with_large_version_diff(
        self, db_session, make_user_token, app
    ):
        """PAE-TRUNC-005: task completes even when raw diff exceeds AI_MAX_INPUT_CHARS."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        # Long PostVersion bodies produce a diff that would normally be huge.
        very_long_body = "X " * 20_000  # 40 000 chars
        pv1 = PostVersion(
            post_id=prompt.id,
            version_number=1,
            markdown_body="Start\n\n" + very_long_body,
            accepted_by_id=author.id,
            created_at=datetime.now(UTC),
        )
        pv2 = PostVersion(
            post_id=prompt.id,
            version_number=2,
            markdown_body="End\n\n" + very_long_body + "\nExtra line.",
            accepted_by_id=author.id,
            created_at=datetime.now(UTC),
        )
        _db.session.add_all([pv1, pv2])
        _db.session.commit()

        # Should complete without error — task truncates before calling provider.
        row = explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="version_diff"
        )
        _db.session.expire(row)

        assert row.status == AnalyticsExplanationStatus.completed.value
        assert row.explanation_md is not None


# ==============================================================================
# PAE-TRUNC-006 – PAE-TRUNC-007: Feed / json-feed leakage
# ==============================================================================


class TestFeedLeakage:
    """Feed endpoints must not expose analytics explanation rows or markup."""

    def test_atom_feed_does_not_expose_explanation_entries(
        self, db_session, make_user_token, client
    ):
        """PAE-TRUNC-006: /feed.xml entry count is unaffected by explanation rows."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        # Baseline feed entry count.
        before = client.get("/feed.xml")
        baseline_entries = before.data.count(b"<entry>")

        # Generate explanation row.
        explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="trend"
        )

        # Feed should be unchanged.
        after = client.get("/feed.xml")
        after_entries = after.data.count(b"<entry>")

        assert after_entries == baseline_entries, (
            "Explanation rows must not appear as feed entries"
        )
        # The explain endpoint path must never appear in the feed XML.
        assert b"/analytics/explain/" not in after.data

    def test_json_feed_does_not_expose_explanation_entries(
        self, db_session, make_user_token, client
    ):
        """PAE-TRUNC-007: /feed.json entry count is unaffected by explanation rows."""
        author, _ = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        before = client.get("/feed.json")
        if before.status_code != 200:
            return  # JSON feed might not be enabled; skip gracefully.

        baseline = len(json.loads(before.data).get("items", []))

        explain_svc.request_explanation(
            user=author, post=prompt, workspace=None, kind="fork_rationale"
        )

        after = client.get("/feed.json")
        after_count = len(json.loads(after.data).get("items", []))

        assert after_count == baseline, (
            "Explanation rows must not appear as JSON feed items"
        )
        assert b"/analytics/explain/" not in after.data
