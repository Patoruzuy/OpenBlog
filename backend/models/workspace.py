"""Workspace model — private containers for versioned documents.

A workspace gives a team a scoped, private writing environment that reuses
the existing revision / diff / release-note machinery without leaking any
content into public feeds, sitemap, explore, or search.

Visibility
----------
Only ``private`` is supported for now.  The enum is future-proofed for a
``public`` workspace that would behave like a shared, published space.

Member roles
------------
owner       — full access; automatically added on workspace creation.
editor      — create/edit documents; accept revisions.
contributor — submit revisions only; cannot accept or edit directly.
viewer      — read-only; cannot create or edit anything.

Isolation guarantee
-------------------
Every query that surfaces public content MUST explicitly include
``WHERE workspace_id IS NULL``.  This module owns the enum definitions that
make that contract explicit in application code.
"""

from __future__ import annotations

import enum
import re
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class WorkspaceVisibility(str, enum.Enum):
    private = "private"
    # public = "public"  # reserved for future phase


class WorkspaceMemberRole(str, enum.Enum):
    owner = "owner"
    editor = "editor"
    contributor = "contributor"
    viewer = "viewer"

    @property
    def rank(self) -> int:
        """Return a numeric privilege rank (higher = more power)."""
        return {"owner": 40, "editor": 30, "contributor": 20, "viewer": 10}[
            self.value
        ]

    def meets(self, required: WorkspaceMemberRole) -> bool:
        """Return True if this role is at least as privileged as *required*."""
        return self.rank >= required.rank


def _workspace_slugify(text: str) -> str:
    """Lightweight slug normaliser for workspace names."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "workspace"


class Workspace(db.Model):
    """A private container scoping a team of users and their documents."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    visibility: Mapped[WorkspaceVisibility] = mapped_column(
        Enum(WorkspaceVisibility, name="workspace_visibility"),
        nullable=False,
        default=WorkspaceVisibility.private,
        server_default=WorkspaceVisibility.private.value,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    owner: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[owner_id],
    )
    members: Mapped[list[WorkspaceMember]] = relationship(
        "WorkspaceMember",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    # documents relationship via Post.workspace_id (set in Post model)

    def __repr__(self) -> str:
        return f"<Workspace id={self.id} slug={self.slug!r}>"


class WorkspaceMember(db.Model):
    """Association between a User and a Workspace with an assigned role."""

    __tablename__ = "workspace_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    workspace_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[WorkspaceMemberRole] = mapped_column(
        Enum(WorkspaceMemberRole, name="workspace_member_role"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    workspace: Mapped[Workspace] = relationship(
        "Workspace",
        back_populates="members",
    )
    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_members_workspace_user",
        ),
        Index(
            "ix_workspace_members_workspace_user",
            "workspace_id",
            "user_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceMember workspace={self.workspace_id} "
            f"user={self.user_id} role={self.role.value!r}>"
        )


# Allowed roles for an invitation — owner cannot be invited directly.
_INVITE_ROLES = ("editor", "contributor", "viewer")


class WorkspaceInvitation(db.Model):  # type: ignore[misc]
    """A time-limited, token-gated invitation to join a workspace.

    Security model
    --------------
    Raw tokens are **never** stored.  Only ``token_hash`` (SHA-256 hex of the
    raw token) is persisted.  The raw token is shown once at creation time
    and then discarded by the server.  An attacker who reads the database
    cannot derive any usable invite URL from ``token_hash`` alone.

    Roles
    -----
    Only ``editor``, ``contributor``, or ``viewer`` may be granted via invite.
    ``owner`` can only be assigned via :func:`~backend.services.workspace_service.change_member_role`.

    Expiry / revocation
    -------------------
    An invitation is invalid when **any** of these conditions hold:
      - ``expires_at < utcnow``
      - ``revoked_at IS NOT NULL``
      - ``uses >= max_uses``
    """

    __tablename__ = "workspace_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    workspace_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    invited_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # 64-char SHA-256 hex digest — never the raw token.
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    # 'editor' | 'contributor' | 'viewer'
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    accepted_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Relationships ──────────────────────────────────────────────────────
    workspace: Mapped[Workspace] = relationship(
        "Workspace",
        foreign_keys=[workspace_id],
    )
    invited_by: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[invited_by_user_id],
    )
    accepted_by: Mapped[User | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[accepted_by_user_id],
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_workspace_invitations_token_hash"),
        CheckConstraint(
            "role IN ('editor', 'contributor', 'viewer')",
            name="ck_workspace_invitations_role",
        ),
        CheckConstraint("max_uses >= 1", name="ck_workspace_invitations_max_uses"),
        CheckConstraint("uses >= 0", name="ck_workspace_invitations_uses_nonneg"),
        Index("idx_workspace_invites_workspace_id", "workspace_id"),
        Index("idx_workspace_invites_token_hash", "token_hash"),
    )

    # ── Derived properties ─────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        """True when the invitation's expiry time has passed.

        SQLite stores datetimes as naive strings; PostgreSQL returns
        timezone-aware values.  Normalise both sides to UTC-aware before
        comparing so tests pass under either backend.
        """
        now = datetime.now(UTC)
        exp = self.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return now > exp

    @property
    def is_revoked(self) -> bool:
        """True when the invitation has been explicitly revoked."""
        return self.revoked_at is not None

    @property
    def is_used_up(self) -> bool:
        """True when max_uses redemptions have already occurred."""
        return self.uses >= self.max_uses

    @property
    def is_valid(self) -> bool:
        """True when the invitation can still be redeemed."""
        return not self.is_expired and not self.is_revoked and not self.is_used_up

    def __repr__(self) -> str:
        return (
            f"<WorkspaceInvitation workspace={self.workspace_id} "
            f"role={self.role!r} valid={self.is_valid}>"
        )
