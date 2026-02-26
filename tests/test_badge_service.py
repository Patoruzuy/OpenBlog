"""Tests for BadgeService."""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.badge import UserBadge
from backend.services.badge_service import BadgeError, BadgeService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def user(make_user_token):
    u, _ = make_user_token("badge_user@example.com", "badgeuser")
    return u


@pytest.fixture()
def other_user(make_user_token):
    u, _ = make_user_token("other@example.com", "otheruser")
    return u


@pytest.fixture()
def seeded(db_session):
    """Ensure default badge definitions exist."""
    return BadgeService.seed_defaults()


# ── seed_defaults ─────────────────────────────────────────────────────────────


class TestSeedDefaults:
    def test_creates_all_defaults(self, db_session):
        badges = BadgeService.seed_defaults()
        assert len(badges) >= 4

    def test_idempotent(self, db_session):
        first = BadgeService.seed_defaults()
        second = BadgeService.seed_defaults()
        assert len(first) == len(second)
        first_ids = {b.id for b in first}
        second_ids = {b.id for b in second}
        assert first_ids == second_ids

    def test_first_accepted_revision_present(self, db_session):
        BadgeService.seed_defaults()
        badge = BadgeService.get_by_key("first_accepted_revision")
        assert badge is not None
        assert badge.name == "First Contribution"

    def test_all_default_keys_present(self, db_session):
        BadgeService.seed_defaults()
        for key in (
            "first_accepted_revision",
            "prolific_author",
            "helpful_commenter",
            "popular_post",
        ):
            assert BadgeService.get_by_key(key) is not None


# ── award ─────────────────────────────────────────────────────────────────────


class TestAward:
    def test_awards_badge_returns_user_badge(self, user, seeded, db_session):
        result = BadgeService.award(user.id, "first_accepted_revision")
        assert result is not None
        assert isinstance(result, UserBadge)
        assert result.user_id == user.id

    def test_award_idempotent_returns_none(self, user, seeded, db_session):
        BadgeService.award(user.id, "prolific_author")
        second = BadgeService.award(user.id, "prolific_author")
        assert second is None

    def test_award_does_not_duplicate_row(self, user, seeded, db_session):
        BadgeService.award(user.id, "helpful_commenter")
        BadgeService.award(user.id, "helpful_commenter")
        from sqlalchemy import func, select

        count = db.session.scalar(
            select(func.count()).where(UserBadge.user_id == user.id)
        )
        assert count == 1

    def test_unknown_badge_key_raises_404(self, user, seeded, db_session):
        with pytest.raises(BadgeError) as exc_info:
            BadgeService.award(user.id, "nonexistent_badge_xyz")
        assert exc_info.value.status_code == 404

    def test_unknown_user_raises_404(self, seeded, db_session):
        with pytest.raises(BadgeError) as exc_info:
            BadgeService.award(99999, "first_accepted_revision")
        assert exc_info.value.status_code == 404

    def test_lazy_seed_on_unknown_key(self, user, db_session):
        """award() auto-seeds defaults; custom unknown key still raises 404."""
        with pytest.raises(BadgeError) as exc_info:
            BadgeService.award(user.id, "totally_fake_key")
        assert exc_info.value.status_code == 404

    def test_different_users_can_earn_same_badge(
        self, user, other_user, seeded, db_session
    ):
        r1 = BadgeService.award(user.id, "popular_post")
        r2 = BadgeService.award(other_user.id, "popular_post")
        assert r1 is not None
        assert r2 is not None


# ── has_badge ─────────────────────────────────────────────────────────────────


class TestHasBadge:
    def test_false_before_award(self, user, seeded, db_session):
        assert BadgeService.has_badge(user.id, "first_accepted_revision") is False

    def test_true_after_award(self, user, seeded, db_session):
        BadgeService.award(user.id, "first_accepted_revision")
        assert BadgeService.has_badge(user.id, "first_accepted_revision") is True

    def test_unknown_key_returns_false(self, user, db_session):
        assert BadgeService.has_badge(user.id, "ghost_badge") is False

    def test_does_not_cross_users(self, user, other_user, seeded, db_session):
        BadgeService.award(user.id, "prolific_author")
        assert BadgeService.has_badge(other_user.id, "prolific_author") is False


# ── list_for_user ─────────────────────────────────────────────────────────────


class TestListForUser:
    def test_empty_before_award(self, user, seeded, db_session):
        assert BadgeService.list_for_user(user.id) == []

    def test_returns_awarded_badges(self, user, seeded, db_session):
        BadgeService.award(user.id, "first_accepted_revision")
        BadgeService.award(user.id, "prolific_author")
        results = BadgeService.list_for_user(user.id)
        assert len(results) == 2

    def test_ordered_newest_first(self, user, seeded, db_session):
        BadgeService.award(user.id, "first_accepted_revision")
        BadgeService.award(user.id, "prolific_author")
        results = BadgeService.list_for_user(user.id)
        # Most recently awarded should come first.
        assert results[0].awarded_at >= results[-1].awarded_at

    def test_isolated_per_user(self, user, other_user, seeded, db_session):
        BadgeService.award(user.id, "helpful_commenter")
        assert BadgeService.list_for_user(other_user.id) == []


# ── list_all_definitions ──────────────────────────────────────────────────────


class TestListAllDefinitions:
    def test_returns_all_seeded_badges(self, seeded, db_session):
        badges = BadgeService.list_all_definitions()
        keys = {b.key for b in badges}
        assert "first_accepted_revision" in keys
        assert "prolific_author" in keys

    def test_empty_when_no_badges(self, db_session):
        assert BadgeService.list_all_definitions() == []


# ── Integration: RevisionService awards badge on accept ───────────────────────


class TestRevisionBadgeIntegration:
    def test_first_accepted_revision_badge_awarded(self, db_session, make_user_token):
        from backend.models.post import Post, PostStatus
        from backend.services.revision_service import RevisionService

        author, _ = make_user_token("badge_author@example.com", "badgeauthor")
        contrib, _ = make_user_token(
            "badge_contrib@example.com", "badgecontrib", role="contributor"
        )
        editor, _ = make_user_token(
            "badge_editor@example.com", "badgeeditor", role="editor"
        )

        post = Post(
            author_id=author.id,
            slug="badge-test-post",
            title="Badge Test Post",
            markdown_body="# Original",
            status=PostStatus.published,
        )
        db.session.add(post)
        db.session.commit()

        BadgeService.seed_defaults()

        rev = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown="# Original\n\nImproved.",
            summary="Improve",
        )
        RevisionService.accept(rev.id, reviewer_id=editor.id)

        assert BadgeService.has_badge(contrib.id, "first_accepted_revision") is True

    def test_second_accepted_revision_does_not_duplicate_badge(
        self, db_session, make_user_token
    ):
        from sqlalchemy import func, select

        from backend.models.post import Post, PostStatus
        from backend.services.revision_service import RevisionService

        author, _ = make_user_token("dup_author@example.com", "dupauthor")
        contrib, _ = make_user_token(
            "dup_contrib@example.com", "dupcontrib", role="contributor"
        )
        editor, _ = make_user_token(
            "dup_editor@example.com", "dupeditor", role="editor"
        )

        post = Post(
            author_id=author.id,
            slug="dup-badge-post",
            title="Dup Badge Post",
            markdown_body="# Version 1",
            status=PostStatus.published,
        )
        db.session.add(post)
        db.session.commit()

        BadgeService.seed_defaults()

        rev1 = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown="# Version 2",
            summary="v2",
        )
        RevisionService.accept(rev1.id, reviewer_id=editor.id)

        rev2 = RevisionService.submit(
            post_id=post.id,
            author_id=contrib.id,
            proposed_markdown="# Version 3",
            summary="v3",
        )
        RevisionService.accept(rev2.id, reviewer_id=editor.id)

        count = db.session.scalar(
            select(func.count()).where(UserBadge.user_id == contrib.id)
        )
        assert count == 1
