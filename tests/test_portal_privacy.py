"""Tests for the User Portal privacy system.

Covers
------
* PrivacyService.get_or_create_privacy()
* PrivacyService.get_public_view() — visibility gates + fine-grained toggles
* ProfileService.update_profile() — field validation
* ProfileService.update_privacy() — enum validation
* ContributionIdentityService.snapshot_for() — mode-specific output
* ContributionIdentityService.render_public() — display-safe output
"""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.portal import IdentityMode, ProfileVisibility
from backend.services.contribution_identity_service import ContributionIdentityService
from backend.services.privacy_service import PrivacyService
from backend.services.profile_service import ProfileService, ProfileServiceError

# ─────────────────────────────────────────────────────────────────────────────
# Per-test user fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token):
    user, _ = make_user_token("alice@portal.com", "portal_alice")
    return user


@pytest.fixture()
def bob(make_user_token):
    user, _ = make_user_token("bob@portal.com", "portal_bob")
    return user


# ─────────────────────────────────────────────────────────────────────────────
# PrivacyService — get_or_create_privacy
# ─────────────────────────────────────────────────────────────────────────────


class TestGetOrCreatePrivacy:
    def test_creates_row_on_first_call(self, db_session, alice):
        settings = PrivacyService.get_or_create_privacy(alice)
        assert settings is not None
        assert settings.user_id == alice.id
        assert settings.profile_visibility == ProfileVisibility.public.value
        assert settings.default_identity_mode == IdentityMode.public.value
        assert settings.show_avatar is True
        assert settings.searchable_profile is True

    def test_returns_existing_row(self, db_session, alice):
        s1 = PrivacyService.get_or_create_privacy(alice)
        s2 = PrivacyService.get_or_create_privacy(alice)
        assert s1.id == s2.id


# ─────────────────────────────────────────────────────────────────────────────
# PrivacyService — get_public_view
# ─────────────────────────────────────────────────────────────────────────────


class TestGetPublicView:
    def test_no_privacy_row_returns_full_view(self, db_session, alice):
        view = PrivacyService.get_public_view(alice, viewer=None)
        assert view["visible"] is True
        assert view["show_bio"] is True
        assert view["show_contributions"] is True

    def test_public_profile_anonymous_viewer_can_see(self, db_session, alice):
        PrivacyService.get_or_create_privacy(alice)  # defaults to public
        view = PrivacyService.get_public_view(alice, viewer=None)
        assert view["visible"] is True

    def test_members_only_hides_from_anonymous(self, db_session, alice):
        s = PrivacyService.get_or_create_privacy(alice)
        s.profile_visibility = ProfileVisibility.members.value
        db.session.commit()
        view = PrivacyService.get_public_view(alice, viewer=None)
        assert view["visible"] is False

    def test_members_only_visible_to_logged_in_user(self, db_session, alice, bob):
        s = PrivacyService.get_or_create_privacy(alice)
        s.profile_visibility = ProfileVisibility.members.value
        db.session.commit()
        view = PrivacyService.get_public_view(alice, viewer=bob)
        assert view["visible"] is True

    def test_private_profile_hidden_from_everyone(self, db_session, alice, bob):
        s = PrivacyService.get_or_create_privacy(alice)
        s.profile_visibility = ProfileVisibility.private.value
        db.session.commit()
        view_anon = PrivacyService.get_public_view(alice, viewer=None)
        view_bob = PrivacyService.get_public_view(alice, viewer=bob)
        assert view_anon["visible"] is False
        assert view_bob["visible"] is False

    def test_private_profile_visible_to_owner(self, db_session, alice):
        s = PrivacyService.get_or_create_privacy(alice)
        s.profile_visibility = ProfileVisibility.private.value
        db.session.commit()
        view = PrivacyService.get_public_view(alice, viewer=alice)
        assert view["visible"] is True

    def test_fine_grained_toggles_respected(self, db_session, alice, bob):
        s = PrivacyService.get_or_create_privacy(alice)
        s.show_bio = False
        s.show_location = False
        db.session.commit()
        view = PrivacyService.get_public_view(alice, viewer=bob)
        assert view["visible"] is True
        assert view["show_bio"] is False
        assert view["show_location"] is False
        assert view["show_avatar"] is True  # unchanged

    def test_owner_always_gets_full_view(self, db_session, alice):
        s = PrivacyService.get_or_create_privacy(alice)
        s.show_bio = False
        s.show_avatar = False
        db.session.commit()
        view = PrivacyService.get_public_view(alice, viewer=alice)
        assert view["show_bio"] is True
        assert view["show_avatar"] is True


# ─────────────────────────────────────────────────────────────────────────────
# ProfileService — update_profile
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateProfile:
    def test_updates_headline(self, db_session, alice):
        ProfileService.update_profile(alice, headline="Senior engineer")
        db.session.refresh(alice)
        assert alice.headline == "Senior engineer"

    def test_headline_too_long_raises(self, db_session, alice):
        with pytest.raises(ProfileServiceError, match="200 characters"):
            ProfileService.update_profile(alice, headline="x" * 201)

    def test_display_name_too_long_raises(self, db_session, alice):
        with pytest.raises(ProfileServiceError, match="128 characters"):
            ProfileService.update_profile(alice, display_name="x" * 129)

    def test_clears_field_with_empty_string(self, db_session, alice):
        alice.bio = "hello"
        db.session.commit()
        ProfileService.update_profile(alice, bio="")
        db.session.refresh(alice)
        assert alice.bio is None

    def test_partial_update_leaves_other_fields_unchanged(self, db_session, alice):
        alice.location = "Berlin"
        db.session.commit()
        ProfileService.update_profile(alice, bio="new bio")
        db.session.refresh(alice)
        assert alice.bio == "new bio"
        assert alice.location == "Berlin"


# ─────────────────────────────────────────────────────────────────────────────
# ProfileService — update_privacy
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdatePrivacy:
    def test_creates_row_and_sets_visibility(self, db_session, alice):
        s = ProfileService.update_privacy(alice, profile_visibility="members")
        assert s.profile_visibility == "members"

    def test_invalid_visibility_raises(self, db_session, alice):
        with pytest.raises(ProfileServiceError, match="Invalid profile visibility"):
            ProfileService.update_privacy(alice, profile_visibility="invisible")

    def test_invalid_identity_mode_raises(self, db_session, alice):
        with pytest.raises(ProfileServiceError, match="Invalid identity mode"):
            ProfileService.update_privacy(alice, default_identity_mode="ghost")

    def test_sets_pseudonymous_alias(self, db_session, alice):
        s = ProfileService.update_privacy(
            alice,
            default_identity_mode="pseudonymous",
            pseudonymous_alias="shadow_coder",
        )
        assert s.default_identity_mode == "pseudonymous"
        assert s.pseudonymous_alias == "shadow_coder"


# ─────────────────────────────────────────────────────────────────────────────
# ContributionIdentityService — snapshot_for
# ─────────────────────────────────────────────────────────────────────────────


class TestSnapshotFor:
    def test_public_mode_captures_display_name_and_avatar(self, db_session, alice):
        alice.display_name = "Alice Smith"
        alice.avatar_url = "https://example.com/a.jpg"
        db.session.commit()
        PrivacyService.get_or_create_privacy(alice)  # default public
        snap = ContributionIdentityService.snapshot_for(alice)
        assert snap["public_identity_mode"] == "public"
        assert snap["public_display_name_snapshot"] == "Alice Smith"
        assert snap["public_avatar_snapshot"] == "https://example.com/a.jpg"

    def test_anonymous_mode_returns_no_name_no_avatar(self, db_session, alice):
        alice.display_name = "Alice Smith"
        alice.avatar_url = "https://example.com/a.jpg"
        db.session.commit()
        s = PrivacyService.get_or_create_privacy(alice)
        s.default_identity_mode = IdentityMode.anonymous.value
        db.session.commit()
        snap = ContributionIdentityService.snapshot_for(alice)
        assert snap["public_identity_mode"] == "anonymous"
        assert snap["public_display_name_snapshot"] is None
        assert snap["public_avatar_snapshot"] is None

    def test_pseudonymous_mode_uses_alias(self, db_session, alice):
        alice.display_name = "Alice Smith"
        db.session.commit()
        s = PrivacyService.get_or_create_privacy(alice)
        s.default_identity_mode = IdentityMode.pseudonymous.value
        s.pseudonymous_alias = "shadow_coder"
        db.session.commit()
        snap = ContributionIdentityService.snapshot_for(alice)
        assert snap["public_identity_mode"] == "pseudonymous"
        assert snap["public_display_name_snapshot"] == "shadow_coder"

    def test_no_privacy_row_defaults_to_public(self, db_session, alice):
        alice.display_name = "Alice Smith"
        db.session.commit()
        snap = ContributionIdentityService.snapshot_for(alice)
        assert snap["public_identity_mode"] == "public"
        assert snap["public_display_name_snapshot"] == "Alice Smith"


# ─────────────────────────────────────────────────────────────────────────────
# ContributionIdentityService — render_public
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderPublic:
    def test_public_mode_renders_name_and_avatar(self, db_session):
        result = ContributionIdentityService.render_public(
            public_identity_mode="public",
            public_display_name_snapshot="Alice Smith",
            public_avatar_snapshot="https://example.com/a.jpg",
        )
        assert result["display_name"] == "Alice Smith"
        assert result["avatar_url"] == "https://example.com/a.jpg"
        assert result["is_anonymous"] is False
        assert result["is_pseudonymous"] is False

    def test_anonymous_mode_returns_anonymous_label(self, db_session):
        result = ContributionIdentityService.render_public(
            public_identity_mode="anonymous",
            public_display_name_snapshot=None,
            public_avatar_snapshot=None,
        )
        assert result["display_name"] == "Anonymous"
        assert result["avatar_url"] is None
        assert result["is_anonymous"] is True

    def test_pseudonymous_mode_shows_alias(self, db_session):
        result = ContributionIdentityService.render_public(
            public_identity_mode="pseudonymous",
            public_display_name_snapshot="shadow_coder",
            public_avatar_snapshot=None,
        )
        assert result["display_name"] == "shadow_coder"
        assert result["is_pseudonymous"] is True
        assert result["is_anonymous"] is False

    def test_no_snapshot_falls_back_to_author(self, db_session, alice):
        alice.display_name = "Fallback User"
        db.session.commit()
        result = ContributionIdentityService.render_public(
            public_identity_mode=None,
            public_display_name_snapshot=None,
            public_avatar_snapshot=None,
            author=alice,
        )
        assert result["display_name"] == "Fallback User"
