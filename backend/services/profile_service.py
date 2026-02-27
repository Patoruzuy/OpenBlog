"""Profile Service.

High-level operations for the User Portal settings screens:
  - profile form    (/settings/profile)
  - privacy form    (/settings/privacy)
  - avatar upload   (/settings/profile with multipart file)

All public methods are static and expect an active Flask application context.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import IO

from backend.extensions import db
from backend.models.portal import IdentityMode, ProfileVisibility, UserPrivacySettings
from backend.models.user import User
from backend.services.privacy_service import PrivacyService
from backend.utils.validation import validate_url

# Allowed MIME types for avatar uploads
_ALLOWED_AVATAR_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
# Maximum avatar file size (bytes) — 2 MiB
_MAX_AVATAR_BYTES = 2 * 1024 * 1024


class ProfileServiceError(Exception):
    """Domain error raised by ProfileService.  Carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ProfileService:
    """High-level operations for profile + privacy settings."""

    # ── Profile form ──────────────────────────────────────────────────────────

    @staticmethod
    def update_profile(
        user: User,
        *,
        display_name: str | None = None,
        headline: str | None = None,
        bio: str | None = None,
        location: str | None = None,
        website_url: str | None = None,
        github_url: str | None = None,
        tech_stack: str | None = None,
    ) -> User:
        """Update profile fields on *user*.  Only non-None fields are changed."""
        if display_name is not None:
            v = display_name.strip()
            if len(v) > 128:
                raise ProfileServiceError("Display name must be 128 characters or fewer.")
            user.display_name = v or None
        if headline is not None:
            v = headline.strip()
            if len(v) > 200:
                raise ProfileServiceError("Headline must be 200 characters or fewer.")
            user.headline = v or None
        if bio is not None:
            user.bio = bio.strip() or None
        if location is not None:
            v = location.strip()
            if len(v) > 128:
                raise ProfileServiceError("Location must be 128 characters or fewer.")
            user.location = v or None
        if website_url is not None:
            user.website_url = (
                validate_url(website_url.strip(), field="website_url")
                if website_url.strip()
                else None
            )
        if github_url is not None:
            user.github_url = (
                validate_url(github_url.strip(), field="github_url")
                if github_url.strip()
                else None
            )
        if tech_stack is not None:
            # Normalise comma-separated list: strip spaces around commas
            tags = [t.strip() for t in tech_stack.split(",") if t.strip()]
            user.tech_stack = ", ".join(tags) or None
        db.session.commit()
        return user

    # ── Avatar upload ─────────────────────────────────────────────────────────

    @staticmethod
    def save_avatar(
        user: User,
        file_obj: IO[bytes],
        mime_type: str,
        upload_folder: str,
    ) -> str:
        """Validate and persist an avatar image.

        Returns the relative URL path (/static/uploads/avatars/<filename>).

        Raises
        ------
        ProfileServiceError(400)  on invalid MIME type or oversized file.
        """
        if mime_type not in _ALLOWED_AVATAR_MIMES:
            raise ProfileServiceError(
                "Avatar must be a JPEG, PNG, GIF, or WebP image.", 400
            )

        # Read and size-check
        data = file_obj.read(_MAX_AVATAR_BYTES + 1)
        if len(data) > _MAX_AVATAR_BYTES:
            raise ProfileServiceError("Avatar must be smaller than 2 MiB.", 400)

        # Derive extension from MIME
        _ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        ext = _ext_map[mime_type]
        filename = f"{uuid.uuid4().hex}{ext}"

        dest = Path(upload_folder) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

        # Delete previous avatar file if it was a local upload (not an external URL)
        if user.avatar_url and user.avatar_url.startswith("/static/uploads/avatars/"):
            old_path = Path(upload_folder) / os.path.basename(user.avatar_url)
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass

        relative_url = f"/static/uploads/avatars/{filename}"
        user.avatar_url = relative_url
        db.session.commit()
        return relative_url

    # ── Privacy form ──────────────────────────────────────────────────────────

    @staticmethod
    def update_privacy(
        user: User,
        *,
        profile_visibility: str | None = None,
        default_identity_mode: str | None = None,
        pseudonymous_alias: str | None = None,
        show_avatar: bool | None = None,
        show_bio: bool | None = None,
        show_location: bool | None = None,
        show_social_links: bool | None = None,
        show_repositories: bool | None = None,
        show_contributions: bool | None = None,
        searchable_profile: bool | None = None,
    ) -> UserPrivacySettings:
        """Update privacy settings for *user*, creating the row if absent."""
        settings = PrivacyService.get_or_create_privacy(user)

        if profile_visibility is not None:
            try:
                settings.profile_visibility = ProfileVisibility(profile_visibility).value
            except ValueError:
                raise ProfileServiceError(
                    f"Invalid profile visibility: {profile_visibility!r}", 400
                )
        if default_identity_mode is not None:
            try:
                settings.default_identity_mode = IdentityMode(default_identity_mode).value
            except ValueError:
                raise ProfileServiceError(
                    f"Invalid identity mode: {default_identity_mode!r}", 400
                )
        if pseudonymous_alias is not None:
            v = pseudonymous_alias.strip()
            if len(v) > 80:
                raise ProfileServiceError("Pseudonymous alias must be 80 characters or fewer.")
            settings.pseudonymous_alias = v or None
        if show_avatar is not None:
            settings.show_avatar = show_avatar
        if show_bio is not None:
            settings.show_bio = show_bio
        if show_location is not None:
            settings.show_location = show_location
        if show_social_links is not None:
            settings.show_social_links = show_social_links
        if show_repositories is not None:
            settings.show_repositories = show_repositories
        if show_contributions is not None:
            settings.show_contributions = show_contributions
        if searchable_profile is not None:
            settings.searchable_profile = searchable_profile

        db.session.commit()
        return settings
