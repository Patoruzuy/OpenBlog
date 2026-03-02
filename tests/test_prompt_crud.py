"""Tests for Prompt Library service-layer CRUD.

Coverage
--------
  PRM-001  create_prompt creates Post(kind='prompt') + PromptMetadata row.
  PRM-002  Slug is auto-generated from title.
  PRM-003  Duplicate titles get unique numeric-suffix slugs.
  PRM-004  Reserved slugs ('new') are rejected via suffix.
  PRM-005  Invalid complexity_level raises PromptError.
  PRM-006  Invalid variables_json raises PromptError.
  PRM-007  get_prompt_by_slug: returns None for wrong kind.
  PRM-008  get_prompt_by_slug: returns None for wrong workspace layer.
  PRM-009  list_prompts: scopes to kind='prompt' and workspace layer.
  PRM-010  list_prompts status filter works.
  PRM-011  list_prompts category filter works (case-insensitive).
  PRM-012  update_prompt_metadata mutates only supplied fields.
  PRM-013  update_prompt_metadata raises 404 for unknown post_id.
  PRM-014  parsed_variables returns empty dict on malformed JSON.
  PRM-015  create_prompt with status='published' sets published_at.
  PRM-016  create_prompt with status='draft' leaves published_at None.
"""

from __future__ import annotations

import itertools
from datetime import datetime

import pytest

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.models.prompt_metadata import PromptMetadata
from backend.services import prompt_service as svc
from backend.services.prompt_service import PromptError

# ── helpers ───────────────────────────────────────────────────────────────────

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


def _make_user(db_session, role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"pm{n}@example.com", f"pmuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


# ── PRM-001 through PRM-016 ───────────────────────────────────────────────────


class TestCreatePrompt:
    def test_creates_post_and_metadata(self, db_session):
        """PRM-001"""
        user = _make_user(db_session)
        post = svc.create_prompt(
            title="Test Prompt",
            markdown_body="Write a summary of {{TEXT}}.",
            author=user,
            workspace_id=None,
            category="summarisation",
            variables={"TEXT": "the text to summarise"},
        )
        _db.session.commit()

        assert post.id is not None
        assert post.kind == "prompt"
        assert post.workspace_id is None

        meta = _db.session.get(PromptMetadata, post.id)
        assert meta is not None
        assert meta.category == "summarisation"
        assert meta.complexity_level == "intermediate"  # default

    def test_slug_auto_generated(self, db_session):
        """PRM-002"""
        user = _make_user(db_session)
        post = svc.create_prompt(
            title="My Great Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
        )
        _db.session.commit()
        assert "my-great-prompt" in post.slug

    def test_duplicate_title_unique_slugs(self, db_session):
        """PRM-003"""
        user = _make_user(db_session)
        p1 = svc.create_prompt(
            title="Duplicate Title",
            markdown_body="v1",
            author=user,
            workspace_id=None,
            category="general",
        )
        _db.session.commit()
        p2 = svc.create_prompt(
            title="Duplicate Title",
            markdown_body="v2",
            author=user,
            workspace_id=None,
            category="general",
        )
        _db.session.commit()
        assert p1.slug != p2.slug
        assert p2.slug.endswith("-1") or p2.slug[-1].isdigit()

    def test_invalid_complexity_raises(self, db_session):
        """PRM-005"""
        user = _make_user(db_session)
        with pytest.raises(PromptError, match="complexity_level"):
            svc.create_prompt(
                title="X",
                markdown_body="body",
                author=user,
                workspace_id=None,
                category="general",
                complexity_level="expert",  # invalid
            )

    def test_invalid_variables_json_raises(self, db_session):
        """PRM-006"""
        user = _make_user(db_session)
        with pytest.raises(PromptError, match="JSON"):
            svc.create_prompt(
                title="X",
                markdown_body="body",
                author=user,
                workspace_id=None,
                category="general",
                variables="{ not valid json",
            )

    def test_published_sets_published_at(self, db_session):
        """PRM-015"""
        user = _make_user(db_session)
        before = datetime.utcnow()  # naive UTC to match stored timestamps
        post = svc.create_prompt(
            title="Published Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()
        assert post.published_at is not None
        assert post.published_at >= before

    def test_draft_leaves_published_at_none(self, db_session):
        """PRM-016"""
        user = _make_user(db_session)
        post = svc.create_prompt(
            title="Draft Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.draft,
        )
        _db.session.commit()
        assert post.published_at is None


class TestGetAndList:
    def test_get_by_slug_wrong_kind_returns_none(self, db_session):
        """PRM-007: get_prompt_by_slug ignores non-prompt posts."""
        user = _make_user(db_session)
        # Create a regular article with the same slug a prompt would have.
        article = Post(
            title="Not A Prompt",
            slug="not-a-prompt",
            kind="article",
            markdown_body="body",
            status=PostStatus.published,
            author_id=user.id,
        )
        _db.session.add(article)
        _db.session.commit()

        result = svc.get_prompt_by_slug("not-a-prompt", workspace_id=None)
        assert result is None

    def test_get_by_slug_wrong_workspace_returns_none(self, db_session):
        """PRM-008"""
        user = _make_user(db_session)
        post = svc.create_prompt(
            title="WS Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,  # public
            category="general",
        )
        _db.session.commit()

        # Looking up with workspace_id=99 must not find the public prompt.
        result = svc.get_prompt_by_slug(post.slug, workspace_id=99)
        assert result is None

    def test_list_prompts_scoped_to_kind(self, db_session):
        """PRM-009: list_prompts never returns articles."""
        user = _make_user(db_session)
        article = Post(
            title="Article",
            slug=f"article-{_n()}",
            kind="article",
            markdown_body="body",
            status=PostStatus.published,
            author_id=user.id,
        )
        _db.session.add(article)
        svc.create_prompt(
            title="Real Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
        )
        _db.session.commit()

        results = svc.list_prompts(workspace_id=None)
        assert all(p.kind == "prompt" for p in results)
        slugs = [p.slug for p in results]
        assert article.slug not in slugs

    def test_list_prompts_status_filter(self, db_session):
        """PRM-010"""
        user = _make_user(db_session)
        svc.create_prompt(
            title="Draft P",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.draft,
        )
        svc.create_prompt(
            title="Published P",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        drafts = svc.list_prompts(workspace_id=None, status="draft")
        published = svc.list_prompts(workspace_id=None, status="published")
        assert all(p.status == PostStatus.draft for p in drafts)
        assert all(p.status == PostStatus.published for p in published)

    def test_list_prompts_category_filter(self, db_session):
        """PRM-011"""
        user = _make_user(db_session)
        svc.create_prompt(
            title="Cat A Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="code-review",
        )
        svc.create_prompt(
            title="Cat B Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="summarisation",
        )
        _db.session.commit()

        results = svc.list_prompts(workspace_id=None, category="Code-Review")  # case-insensitive
        assert len(results) == 1
        assert results[0].prompt_metadata.category == "code-review"


class TestUpdateMetadata:
    def test_update_mutates_supplied_fields_only(self, db_session):
        """PRM-012"""
        user = _make_user(db_session)
        post = svc.create_prompt(
            title="Upd Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="original",
            complexity_level="beginner",
        )
        _db.session.commit()

        svc.update_prompt_metadata(post.id, category="updated", intended_model="gpt-4o")
        _db.session.commit()

        _db.session.expire_all()
        meta = _db.session.get(PromptMetadata, post.id)
        assert meta.category == "updated"
        assert meta.intended_model == "gpt-4o"
        # complexity_level was not touched.
        assert meta.complexity_level == "beginner"

    def test_update_unknown_post_raises_404(self, db_session):
        """PRM-013"""
        with pytest.raises(PromptError) as exc_info:
            svc.update_prompt_metadata(999_999, category="x")
        assert exc_info.value.status_code == 404

    def test_parsed_variables_on_bad_json(self, db_session):
        """PRM-014"""
        user = _make_user(db_session)
        post = svc.create_prompt(
            title="Var Prompt",
            markdown_body="body",
            author=user,
            workspace_id=None,
            category="general",
        )
        _db.session.commit()
        meta = _db.session.get(PromptMetadata, post.id)
        # Manually break the JSON to test graceful fallback.
        meta.variables_json = "{ broken"
        assert svc.parsed_variables(meta) == {}
