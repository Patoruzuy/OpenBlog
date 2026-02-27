"""Privacy Service.

Single responsibility: apply a user's privacy settings when serving their
public profile to a viewer.

Design principle
----------------
Templates NEVER check privacy settings directly.  All filtering goes through
``PrivacyService.get_public_view(user, viewer)``, which returns a dict that is
always safe to render.  If a profile is hidden, the dict contains only the
minimum fields needed to show a "profile unavailable" stub.
"""

from __future__ import annotations

from backend.models.portal import IdentityMode, ProfileVisibility, UserPrivacySettings
from backend.models.user import User


class PrivacyService:
    """Applies a ``User``'s privacy settings when serving their public profile."""

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def get_or_create_privacy(user: User) -> UserPrivacySettings:
        """Return the privacy settings row for *user*, creating one if absent.

        Always commits to the DB so subsequent reads return the same row.
        """
        from backend.extensions import db  # local to avoid circular import

        if user.privacy_settings is not None:
            return user.privacy_settings

        settings = UserPrivacySettings(user_id=user.id)
        db.session.add(settings)
        db.session.commit()
        # Refresh so the relationship is populated
        db.session.refresh(user)
        return user.privacy_settings  # type: ignore[return-value]

    @staticmethod
    def get_public_view(user: User, viewer: User | None) -> dict:
        """Return a privacy-filtered view of *user*'s profile for *viewer*.

        Returns a dict with these guaranteed keys:

        ``visible``     — bool; False when the profile is completely hidden
        ``user``        — the ``User`` ORM object (always present for routing)
        ``show_avatar`` — bool
        ``show_bio``    — bool
        ``show_location``— bool
        ``show_social_links`` — bool
        ``show_repositories`` — bool
        ``show_contributions``— bool
        ``identity_mode``     — IdentityMode value for this viewer
        """
        privacy = user.privacy_settings
        if privacy is None:
            # No row yet → treat as fully public defaults
            return PrivacyService._full_view(user)

        visibility = ProfileVisibility(privacy.profile_visibility)

        # ── Access gate ───────────────────────────────────────────────────────
        if visibility == ProfileVisibility.private:
            # Owner can still see their own private profile in settings
            if viewer is None or viewer.id != user.id:
                return PrivacyService._hidden_view(user)

        if visibility == ProfileVisibility.members:
            if viewer is None:
                return PrivacyService._hidden_view(user)

        # ── Viewer-is-owner override: always full view ────────────────────────
        if viewer is not None and viewer.id == user.id:
            return PrivacyService._full_view(user)

        # ── Apply fine-grained toggles ────────────────────────────────────────
        return {
            "visible": True,
            "user": user,
            "show_avatar": privacy.show_avatar,
            "show_bio": privacy.show_bio,
            "show_location": privacy.show_location,
            "show_social_links": privacy.show_social_links,
            "show_repositories": privacy.show_repositories,
            "show_contributions": privacy.show_contributions,
            "identity_mode": IdentityMode(privacy.default_identity_mode).value,
        }

    @staticmethod
    def can_view_profile(user: User, viewer: User | None) -> bool:
        """Return True if *viewer* is allowed to see *user*'s profile."""
        return PrivacyService.get_public_view(user, viewer)["visible"]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _full_view(user: User) -> dict:
        return {
            "visible": True,
            "user": user,
            "show_avatar": True,
            "show_bio": True,
            "show_location": True,
            "show_social_links": True,
            "show_repositories": True,
            "show_contributions": True,
            "identity_mode": IdentityMode.public.value,
        }

    @staticmethod
    def _hidden_view(user: User) -> dict:
        return {
            "visible": False,
            "user": user,
            "show_avatar": False,
            "show_bio": False,
            "show_location": False,
            "show_social_links": False,
            "show_repositories": False,
            "show_contributions": False,
            "identity_mode": IdentityMode.anonymous.value,
        }
