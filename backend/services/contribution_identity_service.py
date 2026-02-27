"""Contribution Identity Service.

Handles the snapshot of a user's identity mode at the time they submit a
revision or comment.  The snapshot allows the public display of contributions
to honour the identity mode that was active *at submission time*, even if the
author later changes their privacy settings.

Identity modes
--------------
public        — real display name + avatar
pseudonymous  — custom alias from privacy settings; optional avatar
anonymous     — shown as "Anonymous"; no avatar
"""

from __future__ import annotations

from backend.models.portal import IdentityMode, UserPrivacySettings
from backend.models.user import User


class ContributionIdentityService:
    """Static helpers for snapshotting and rendering contribution identities."""

    # ── Snapshot (called at write time) ───────────────────────────────────────

    @staticmethod
    def snapshot_for(user: User) -> dict:
        """Return a dict of snapshot values to write onto a new contribution.

        The caller should spread this onto the model instance before commit::

            snap = ContributionIdentityService.snapshot_for(current_user)
            revision.public_identity_mode          = snap["public_identity_mode"]
            revision.public_display_name_snapshot  = snap["public_display_name_snapshot"]
            revision.public_avatar_snapshot        = snap["public_avatar_snapshot"]
        """
        privacy: UserPrivacySettings | None = user.privacy_settings
        mode = IdentityMode.public
        if privacy is not None:
            try:
                mode = IdentityMode(privacy.default_identity_mode)
            except ValueError:
                mode = IdentityMode.public

        display_name: str | None
        avatar_url: str | None

        if mode == IdentityMode.anonymous:
            display_name = None
            avatar_url = None
        elif mode == IdentityMode.pseudonymous:
            display_name = (
                privacy.pseudonymous_alias or user.display_name or user.username
                if privacy
                else user.display_name or user.username
            )
            # Only include the avatar if the privacy settings permit it
            avatar_url = (
                user.avatar_url
                if (privacy is None or privacy.show_avatar)
                else None
            )
        else:  # public
            display_name = user.display_name or user.username
            avatar_url = user.avatar_url

        return {
            "public_identity_mode": mode.value,
            "public_display_name_snapshot": display_name,
            "public_avatar_snapshot": avatar_url,
        }

    # ── Render (called at read time) ──────────────────────────────────────────

    @staticmethod
    def render_public(
        *,
        public_identity_mode: str | None,
        public_display_name_snapshot: str | None,
        public_avatar_snapshot: str | None,
        # Fallback — passed so callers can display the author if no snapshot exists
        author: User | None = None,
    ) -> dict:
        """Return a display-safe identity dict for a contribution.

        Safe for template consumption::

            identity = ContributionIdentityService.render_public(
                public_identity_mode=revision.public_identity_mode,
                public_display_name_snapshot=revision.public_display_name_snapshot,
                public_avatar_snapshot=revision.public_avatar_snapshot,
                author=revision.author,
            )
            # identity["display_name"], identity["avatar_url"], identity["is_anonymous"]
        """
        if public_identity_mode is None and author is not None:
            # Legacy contribution before snapshotting was introduced — fall back
            # to the author's current public-mode identity.
            snap = ContributionIdentityService.snapshot_for(author)
            public_identity_mode = snap["public_identity_mode"]
            public_display_name_snapshot = snap["public_display_name_snapshot"]
            public_avatar_snapshot = snap["public_avatar_snapshot"]

        mode_str = public_identity_mode or IdentityMode.public.value
        is_anonymous = mode_str == IdentityMode.anonymous.value

        return {
            "mode": mode_str,
            "display_name": public_display_name_snapshot if not is_anonymous else "Anonymous",
            "avatar_url": public_avatar_snapshot if not is_anonymous else None,
            "is_anonymous": is_anonymous,
            "is_pseudonymous": mode_str == IdentityMode.pseudonymous.value,
        }
