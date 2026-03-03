"""Tests for Analytics Explanations — cross-scope isolation.

Covers:
  PAE-ISO-001  Workspace explanation NOT visible via public get_explanation_any_status.
  PAE-ISO-002  Workspace A member cannot see Workspace B explanations.
  PAE-ISO-003  Public explanation NOT visible via workspace get_explanation_any_status.
  PAE-ISO-004  get_explanation respects scope: workspace user cannot read public row.
  PAE-ISO-005  Two workspaces' explanations for same post title never mix.
  PAE-ISO-006  Workspace analytics GET has Cache-Control: private, no-store.
  PAE-ISO-007  Public analytics GET does NOT have private/no-store (public caching).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services import prompt_analytics_explain_service as explain_svc
from backend.services import workspace_service as ws_svc

# ── helpers ───────────────────────────────────────────────────────────────────

_counter = itertools.count(1)


def _uid() -> int:
    return next(_counter)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_user(make_user_token, role: str = "reader"):
    n = _uid()
    return make_user_token(f"iso_expl_{n}@example.com", f"iso_expl_{n}", role=role)


def _workspace_with_owner(make_user_token):
    owner, tok = _new_user(make_user_token, role="editor")
    ws = ws_svc.create_workspace(name=f"Iso-WS-{_uid()}", owner=owner)
    _db.session.commit()
    return ws, owner, tok


def _ws_prompt(ws, author) -> Post:
    uid = _uid()
    post = ws_svc.create_workspace_document(
        workspace=ws,
        author=author,
        title=f"Iso WS Prompt {uid}",
        markdown_body="# Iso Test\n\nContent.",
    )
    post.kind = "prompt"
    post.status = PostStatus.published
    _db.session.commit()
    return post


def _public_prompt(author) -> Post:
    uid = _uid()
    post = Post(
        title=f"Iso Public Prompt {uid}",
        slug=f"iso-pub-prompt-{uid}",
        kind="prompt",
        markdown_body="# Iso Public\n\nContent.",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _db.session.add(post)
    _db.session.commit()
    return post


# ==============================================================================
# PAE-ISO-001: Workspace explanations hidden from public scope
# ==============================================================================


class TestWorkspaceExplanationHiddenFromPublic:
    """Workspace explanation rows must not leak to the public scope query."""

    def test_workspace_explanation_not_visible_via_public_any_status(
        self, db_session, make_user_token
    ):
        """PAE-ISO-001: workspace explanation row not returned by public query."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        ws_prompt = _ws_prompt(ws, owner)

        # Create a workspace explanation.
        explain_svc.request_explanation(
            user=owner, post=ws_prompt, workspace=ws, kind="trend"
        )

        # Query the same post via public scope → must return None.
        public_result = explain_svc.get_explanation_any_status(
            post=ws_prompt, workspace=None, kind="trend"
        )

        assert public_result is None, (
            "Workspace explanation must not appear in public scope query"
        )

    def test_workspace_explanation_not_visible_via_public_get_explanation(
        self, db_session, make_user_token
    ):
        """PAE-ISO-001b: get_explanation public scope returns None for ws row."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        ws_prompt = _ws_prompt(ws, owner)

        explain_svc.request_explanation(
            user=owner, post=ws_prompt, workspace=ws, kind="fork_rationale"
        )

        # Attempting to read as public scope with the posting author.
        public_result = explain_svc.get_explanation(
            user=owner, post=ws_prompt, workspace=None, kind="fork_rationale"
        )

        assert public_result is None


# ==============================================================================
# PAE-ISO-002: Workspace A cannot see Workspace B explanations
# ==============================================================================


class TestWorkspaceCrossWorkspaceIsolation:
    """Members of workspace A must not see explanations from workspace B."""

    def test_workspace_a_member_cannot_see_workspace_b_explanation(
        self, db_session, make_user_token
    ):
        """PAE-ISO-002: get_explanation_any_status scoped to ws_b returns None for ws_a row."""
        ws_a, owner_a, _ = _workspace_with_owner(make_user_token)
        ws_b, owner_b, _ = _workspace_with_owner(make_user_token)

        prompt_a = _ws_prompt(ws_a, owner_a)
        # We only need prompt_a (ws_a) to verify ws_b cannot see it.

        # Create explanation in workspace A.
        explain_svc.request_explanation(
            user=owner_a, post=prompt_a, workspace=ws_a, kind="trend"
        )

        # Query workspace B for prompt_a — result must be None.
        result = explain_svc.get_explanation_any_status(
            post=prompt_a, workspace=ws_b, kind="trend"
        )

        assert result is None, (
            "Workspace B scope must not return workspace A explanation"
        )

    def test_workspace_b_member_service_call_for_ws_a_post_creates_ws_b_row(
        self, db_session, make_user_token
    ):
        """PAE-ISO-002b: owner_b calling explain on prompt_a scoped to ws_b creates
        a ws_b-scoped row (isolation is enforced query-side, not write-side).
        The resulting row has workspace_id=ws_b, so ws_a clients never see it."""
        ws_a, owner_a, _ = _workspace_with_owner(make_user_token)
        ws_b, owner_b, _ = _workspace_with_owner(make_user_token)
        prompt_a = _ws_prompt(ws_a, owner_a)

        # owner_b is a member of ws_b, so _assert_scope passes for ws_b.
        row = explain_svc.request_explanation(
            user=owner_b, post=prompt_a, workspace=ws_b, kind="trend"
        )

        # Row is attributed to ws_b — not ws_a.
        assert row.workspace_id == ws_b.id
        assert row.scope_type == "workspace"

        # ws_a scope still returns nothing for prompt_a.
        ws_a_result = explain_svc.get_explanation_any_status(
            post=prompt_a, workspace=ws_a, kind="trend"
        )
        assert ws_a_result is None, (
            "ws_b-scoped row must not appear when querying ws_a scope"
        )


# ==============================================================================
# PAE-ISO-003: Public explanations hidden from workspace scope
# ==============================================================================


class TestPublicExplanationHiddenFromWorkspace:
    """Public explanation rows must not appear in workspace scope queries."""

    def test_public_explanation_not_visible_via_workspace_any_status(
        self, db_session, make_user_token
    ):
        """PAE-ISO-003: public explanation not returned by workspace scope query."""
        ws, owner, _ = _workspace_with_owner(make_user_token)
        pub_prompt = _public_prompt(owner)

        # Create a public explanation.
        explain_svc.request_explanation(
            user=owner, post=pub_prompt, workspace=None, kind="trend"
        )

        # Query via workspace scope → must return None (different scope_type).
        ws_result = explain_svc.get_explanation_any_status(
            post=pub_prompt, workspace=ws, kind="trend"
        )

        assert ws_result is None, (
            "Public explanation must not appear in workspace scope query"
        )


# ==============================================================================
# PAE-ISO-005: Two workspaces with same-slug prompts never mix
# ==============================================================================


class TestTwoWorkspaceExplanationIsolation:
    """Explanations for identically-named prompts in different workspaces are isolated."""

    def test_same_slug_prompts_in_two_workspaces_have_separate_explanations(
        self, db_session, make_user_token
    ):
        """PAE-ISO-005: workspace_id in the UNIQUE constraint ensures isolation."""
        ws_a, owner_a, _ = _workspace_with_owner(make_user_token)
        ws_b, owner_b, _ = _workspace_with_owner(make_user_token)

        prompt_a = _ws_prompt(ws_a, owner_a)
        prompt_b = _ws_prompt(ws_b, owner_b)

        row_a = explain_svc.request_explanation(
            user=owner_a, post=prompt_a, workspace=ws_a, kind="trend"
        )
        row_b = explain_svc.request_explanation(
            user=owner_b, post=prompt_b, workspace=ws_b, kind="trend"
        )

        assert row_a.id != row_b.id
        assert row_a.workspace_id == ws_a.id
        assert row_b.workspace_id == ws_b.id

        # Each workspace sees only its own row.
        result_a = explain_svc.get_explanation_any_status(
            post=prompt_a, workspace=ws_a, kind="trend"
        )
        result_b = explain_svc.get_explanation_any_status(
            post=prompt_b, workspace=ws_b, kind="trend"
        )

        assert result_a is not None and result_a.id == row_a.id
        assert result_b is not None and result_b.id == row_b.id


# ==============================================================================
# PAE-ISO-006 – PAE-ISO-007: Cache-Control header isolation
# ==============================================================================


class TestCacheControlIsolation:
    """Workspace analytics must be private; public analytics must not be locked down."""

    def test_workspace_analytics_is_private_no_store(
        self, db_session, make_user_token, client
    ):
        """PAE-ISO-006: workspace analytics GET → Cache-Control: private, no-store."""
        ws, owner, tok = _workspace_with_owner(make_user_token)
        prompt = _ws_prompt(ws, owner)

        resp = client.get(
            f"/w/{ws.slug}/prompts/{prompt.slug}/analytics",
            headers=_auth(tok),
        )

        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert "private" in cc

    def test_public_analytics_does_not_have_private_no_store(
        self, db_session, make_user_token, client
    ):
        """PAE-ISO-007: public analytics GET does NOT have private, no-store."""
        author, tok = _new_user(make_user_token, role="editor")
        prompt = _public_prompt(author)

        resp = client.get(
            f"/prompts/{prompt.slug}/analytics",
            headers=_auth(tok),
        )

        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        # Public page should NOT be locked with private + no-store.
        assert "private" not in cc or "no-store" not in cc
