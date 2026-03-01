"""User portal models.

Four tables that extend the core ``users`` table to support the user-portal
feature set:

* ``UserPrivacySettings``  — per-user visibility and identity-mode config
* ``UserSocialLink``       — ordered list of curated social/external links
* ``UserConnectedAccount`` — OAuth provider tokens (GitHub, etc.)
* ``UserRepository``       — manually added or auto-synced code repositories
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db

# ── Enumerations ──────────────────────────────────────────────────────────────


class IdentityMode(str, enum.Enum):
    """How a user's contributions appear publicly."""

    public = "public"  # real display name + avatar
    pseudonymous = "pseudonymous"  # custom alias + optional avatar
    anonymous = "anonymous"  # "Anonymous" + no avatar


class ProfileVisibility(str, enum.Enum):
    """Who can view a user's public profile page."""

    public = "public"  # anyone
    members = "members"  # logged-in users only
    private = "private"  # nobody (profile hidden from /users/<username>)


# ── Models ────────────────────────────────────────────────────────────────────


class UserPrivacySettings(db.Model):
    """One-to-one extension of ``users``: visibility + identity preferences."""

    __tablename__ = "user_privacy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    profile_visibility: Mapped[str] = mapped_column(
        Enum(ProfileVisibility, name="profile_visibility_enum"),
        nullable=False,
        default=ProfileVisibility.public,
    )
    default_identity_mode: Mapped[str] = mapped_column(
        Enum(IdentityMode, name="identity_mode_enum"),
        nullable=False,
        default=IdentityMode.public,
    )
    # Alias used when identity mode is ``pseudonymous``
    pseudonymous_alias: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Fine-grained visibility toggles (all True by default)
    show_avatar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_bio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_location: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_social_links: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    show_repositories: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    show_contributions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    searchable_profile: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # ── Notification preferences ───────────────────────────────────────────
    # Email me when someone new comments on a post I follow
    notify_thread_emails: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Email me when someone replies directly to my comment
    notify_reply_emails: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationship back to User (declared via string ref to avoid circular import)
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="privacy_settings"
    )

    def __repr__(self) -> str:
        return (
            f"<UserPrivacySettings user_id={self.user_id} "
            f"visibility={self.profile_visibility} "
            f"identity={self.default_identity_mode}>"
        )


class UserSocialLink(db.Model):
    """Ordered list of social / external links shown on a public profile."""

    __tablename__ = "user_social_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Short label displayed on the link chip (e.g. "Twitter", "Portfolio")
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    # Optional icon slug (e.g. "twitter", "linkedin") for CSS icon rendering
    icon_slug: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="social_links"
    )

    def __repr__(self) -> str:
        return f"<UserSocialLink user_id={self.user_id} label={self.label!r}>"


class UserConnectedAccount(db.Model):
    """OAuth / external-provider linked accounts (GitHub first)."""

    __tablename__ = "user_connected_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_connected_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Provider slug (github, gitlab, linkedin …)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    # Provider's own user ID (for deduplication on re-connect)
    provider_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Tokens are stored encrypted; the encryption key lives in config.
    # NULL for manual-link entries that have no token (e.g. user just types
    # their GitHub URL without going through OAuth).
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="connected_accounts"
    )

    def __repr__(self) -> str:
        return (
            f"<UserConnectedAccount user_id={self.user_id} provider={self.provider!r}>"
        )


class RepositorySource(str, enum.Enum):
    """Where a repository record originated."""

    manual = "manual"  # user typed it in
    github = "github"  # synced via GitHub API
    gitlab = "gitlab"  # future
    other = "other"


class UserRepository(db.Model):
    """Code repositories listed on a user's public profile."""

    __tablename__ = "user_repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        Enum(RepositorySource, name="repository_source_enum"),
        nullable=False,
        default=RepositorySource.manual,
    )
    # Human-readable name (often owner/repo)
    repo_name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Star / fork counts cached from the last API sync
    stars_cached: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    forks_cached: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Provider-side ID for deduplication during syncs
    external_repo_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="repositories"
    )

    def __repr__(self) -> str:
        return f"<UserRepository user_id={self.user_id} repo={self.repo_name!r}>"
