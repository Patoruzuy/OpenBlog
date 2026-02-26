"""User model.

Roles
-----
admin       — full platform access; can manage all content and users
editor      — can edit/approve any post; cannot manage users
contributor — can propose revisions; can author own posts
reader      — default; read-only; can comment and vote
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class UserRole(str, enum.Enum):
    admin = "admin"
    editor = "editor"
    contributor = "contributor"
    reader = "reader"


class User(db.Model):
    """Platform user.

    Passwords are stored as argon2 hashes via ``argon2-cffi``.
    OAuth users may have a null ``password_hash``; authentication is via
    the ``oauth_provider`` / ``oauth_id`` pair.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Identity ───────────────────────────────────────────────────────────
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # ── OAuth ──────────────────────────────────────────────────────────────
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── Role & status ──────────────────────────────────────────────────────
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.reader,
        server_default=UserRole.reader.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Shadow-ban: user sees own content as normal; others do not see it.
    is_shadow_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Reputation ─────────────────────────────────────────────────────────
    reputation_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Profile ────────────────────────────────────────────────────────────
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tech_stack: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Comma-separated tech tags, e.g. 'Python,Flask,PostgreSQL'"
    )
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    posts: Mapped[list[Post]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Post", back_populates="author", lazy="select"
    )
    revisions: Mapped[list[Revision]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Revision",
        foreign_keys="[Revision.author_id]",
        back_populates="author",
        lazy="select",
    )
    comments: Mapped[list[Comment]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Comment", back_populates="author", lazy="select"
    )
    badges: Mapped[list[UserBadge]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "UserBadge", back_populates="user", lazy="select"
    )
    notifications: Mapped[list[Notification]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Notification", back_populates="user", lazy="select"
    )

    # ── Composite indexes ──────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_users_oauth", "oauth_provider", "oauth_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role.value!r}>"
