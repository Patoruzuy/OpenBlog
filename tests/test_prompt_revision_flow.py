"""Tests for Prompt Library versioning via the existing revision system.

Coverage
--------
  PRF-001  RevisionService.submit works on a prompt post_id.
  PRF-002  RevisionService.accept on a prompt increments post.version.
  PRF-003  After accept, post.markdown_body reflects proposed_markdown.
  PRF-004  Accepted revision creates a PostVersion row for the prompt.
  PRF-005  update_prompt_metadata is independent of revision flow.
  PRF-006  Multiple successive revisions form a correct history chain.
"""

from __future__ import annotations

import itertools

import pytest

from backend.extensions import db as _db
from backend.models.post import PostStatus
from backend.models.post_version import PostVersion
from backend.models.revision import RevisionStatus
from backend.services import prompt_service as svc
from backend.services.revision_service import RevisionService

_ctr = itertools.count(200)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.services.auth_service import AuthService

    n = _n()
    return AuthService.register(f"rv{n}@example.com", f"rvuser{n}", "StrongPass123!!")


# ── PRF-001: submit a revision against a prompt ────────────────────────────────


class TestRevisionSubmit:
    def test_submit_works_on_prompt(self, db_session):
        """PRF-001"""
        author = _make_user()
        contributor = _make_user()
        post = svc.create_prompt(
            title="Prompt For Revision",
            markdown_body="Original body with {{VAR}}.",
            author=author,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        rev = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,
            proposed_markdown="Updated body with {{VAR}}.",
            summary="Clarified wording",
        )
        _db.session.commit()

        assert rev.id is not None
        assert rev.post_id == post.id
        assert rev.status == RevisionStatus.pending

    def test_submit_proposed_markdown_stored(self, db_session):
        author = _make_user()
        contributor = _make_user()
        post = svc.create_prompt(
            title="Store Test Prompt",
            markdown_body="Original.",
            author=author,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        rev = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,
            proposed_markdown="Improved on original.",
            summary="Improvement",
        )
        _db.session.commit()
        assert rev.proposed_markdown == "Improved on original."


# ── PRF-002/003: accept bumps version and updates body ────────────────────────


class TestRevisionAccept:
    def _setup_pending(self, db_session):
        author = _make_user()
        contributor = _make_user()
        editor = _make_user()
        from backend.models.user import UserRole
        editor.role = UserRole.editor
        _db.session.flush()

        post = svc.create_prompt(
            title="Accept Test Prompt",
            markdown_body="v1 body.",
            author=author,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        rev = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,  # must differ from post author
            proposed_markdown="v2 body.",
            summary="v2 update",
        )
        _db.session.commit()
        return post, rev, editor

    def test_accept_increments_version(self, db_session):
        """PRF-002"""
        post, rev, editor = self._setup_pending(db_session)
        v_before = post.version or 1

        RevisionService.accept(revision_id=rev.id, reviewer_id=editor.id)
        _db.session.commit()
        _db.session.expire_all()

        post_after = _db.session.get(type(post), post.id)
        assert (post_after.version or 1) > v_before

    def test_accept_updates_markdown_body(self, db_session):
        """PRF-003"""
        post, rev, editor = self._setup_pending(db_session)

        RevisionService.accept(revision_id=rev.id, reviewer_id=editor.id)
        _db.session.commit()
        _db.session.expire_all()

        post_after = _db.session.get(type(post), post.id)
        assert post_after.markdown_body == "v2 body."

    def test_accept_creates_post_version_row(self, db_session):
        """PRF-004"""
        post, rev, editor = self._setup_pending(db_session)

        RevisionService.accept(revision_id=rev.id, reviewer_id=editor.id)
        _db.session.commit()

        pv = (
            _db.session.query(PostVersion)
            .filter_by(post_id=post.id)
            .first()
        )
        assert pv is not None
        assert pv.post_id == post.id


# ── PRF-005: metadata and versioning are independent ─────────────────────────


class TestMetadataIndependentOfRevision:
    def test_metadata_update_does_not_affect_version(self, db_session):
        """PRF-005"""
        author = _make_user()
        post = svc.create_prompt(
            title="Independent Meta",
            markdown_body="body.",
            author=author,
            workspace_id=None,
            category="original-cat",
        )
        _db.session.commit()
        v_before = post.version

        svc.update_prompt_metadata(post.id, category="updated-cat")
        _db.session.commit()
        _db.session.expire_all()

        post_after = _db.session.get(type(post), post.id)
        assert post_after.version == v_before  # version unchanged
        from backend.models.prompt_metadata import PromptMetadata
        meta = _db.session.get(PromptMetadata, post.id)
        assert meta.category == "updated-cat"


# ── PRF-006: successive revisions ─────────────────────────────────────────────


class TestSuccessiveRevisions:
    def test_two_successive_revisions(self, db_session):
        """PRF-006"""
        from backend.models.user import UserRole

        author = _make_user()
        editor = _make_user()
        editor.role = UserRole.editor
        _db.session.flush()

        post = svc.create_prompt(
            title="Multi Rev Prompt",
            markdown_body="v1.",
            author=author,
            workspace_id=None,
            category="general",
            status=PostStatus.published,
        )
        _db.session.commit()

        contrib = _make_user()

        # First revision.
        r1 = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,  # must differ from post author
            proposed_markdown="v2.",
            summary="v2",
        )
        _db.session.commit()
        RevisionService.accept(revision_id=r1.id, reviewer_id=editor.id)
        _db.session.commit()

        # Second revision.
        r2 = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown="v3.",
            summary="v3",
        )
        _db.session.commit()
        RevisionService.accept(revision_id=r2.id, reviewer_id=editor.id)
        _db.session.commit()
        _db.session.expire_all()

        post_final = _db.session.get(type(post), post.id)
        assert post_final.markdown_body == "v3."

        versions = (
            _db.session.query(PostVersion)
            .filter_by(post_id=post.id)
            .order_by(PostVersion.created_at)
            .all()
        )
        assert len(versions) >= 2
