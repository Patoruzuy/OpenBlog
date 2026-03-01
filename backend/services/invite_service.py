"""Workspace invitation service — create, validate, redeem, and revoke invites.

Token security
--------------
Raw tokens are generated via :func:`~backend.security.tokens.generate_invite_token`
(32-byte URLsafe random = 256-bit entropy).  **Only** the SHA-256 hash of the
raw token is persisted in ``workspace_invitations.token_hash``.  A DB leak
cannot yield usable invite URLs.

The raw token is the caller's responsibility to display/persist at creation
time — this service discards it immediately after returning.

Non-leakage guarantee
---------------------
:func:`validate_invite` returns a simple status string (``"valid"``,
``"expired"``, ``"revoked"``, ``"used_up"``, ``"not_found"``) with **no**
workspace name, slug, or any other identifying information.  Routes that
call this function MUST NOT include workspace details in the error response.

Ownership invariants
--------------------
- Only workspace owners (or platform admins) can create/revoke invites.
- ``owner`` cannot be assigned via invite — only through
  :func:`~backend.services.workspace_service.change_member_role`.
- Invitations can be redeemed idempotently: if the user is already a member,
  the invite is marked accepted and the existing membership is returned.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.extensions import db
from backend.models.user import User
from backend.models.workspace import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from backend.security.tokens import generate_invite_token, hash_token

# Roles that can be granted via invite; owner must be promoted explicitly.
_ALLOWED_INVITE_ROLES: frozenset[str] = frozenset({"editor", "contributor", "viewer"})

# Convenience status strings returned by validate_invite().
_VALID = "valid"
_EXPIRED = "expired"
_REVOKED = "revoked"
_USED_UP = "used_up"
_NOT_FOUND = "not_found"


# ── Internal helpers ──────────────────────────────────────────────────────────


def _is_site_admin(user: User) -> bool:
    return (
        user is not None
        and getattr(user, "role", None) is not None
        and user.role.value == "admin"
    )


def _require_owner_or_admin(
    workspace: Workspace, actor: User, *, op_name: str = "this operation"
) -> None:
    """Raise :exc:`PermissionError` if *actor* is not owner/admin for *workspace*."""
    if _is_site_admin(actor):
        return
    member = db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == actor.id,
        )
    )
    if member is None or not member.role.meets(WorkspaceMemberRole.owner):
        raise PermissionError(
            f"Only workspace owners or site admins can perform {op_name}"
        )


# ── Public API ────────────────────────────────────────────────────────────────


def create_invite(
    workspace: Workspace,
    invited_by: User,
    role: str,
    *,
    expires_in_days: int = 7,
    max_uses: int = 1,
) -> tuple[WorkspaceInvitation, str]:
    """Create a workspace invitation and return ``(invite, raw_token)``.

    The *raw_token* is shown once at creation time — it is never stored in the
    database.  Callers MUST commit after this call.

    Parameters
    ----------
    workspace:
        The workspace to issue the invitation for.
    invited_by:
        The authenticated user creating the invitation (must be owner/admin).
    role:
        Target membership role: ``"editor"``, ``"contributor"``, or
        ``"viewer"``.  Raises :exc:`ValueError` for ``"owner"`` or any
        unknown value.
    expires_in_days:
        How many days until the invitation expires (default 7).
    max_uses:
        Maximum redemptions allowed (default 1, single-use).

    Returns
    -------
    tuple[WorkspaceInvitation, str]
        The created ORM object (flushed, not committed) and the raw token.

    Raises
    ------
    PermissionError
        If *invited_by* is not a workspace owner or site admin.
    ValueError
        If *role* is invalid or *max_uses* < 1.
    """
    _require_owner_or_admin(workspace, invited_by, op_name="invite creation")

    role = role.lower().strip()
    if role not in _ALLOWED_INVITE_ROLES:
        raise ValueError(
            f"Invalid invite role {role!r}. "
            f"Allowed: {sorted(_ALLOWED_INVITE_ROLES)}"
        )
    if max_uses < 1:
        raise ValueError("max_uses must be at least 1")
    if expires_in_days < 1:
        raise ValueError("expires_in_days must be at least 1")

    raw_token = generate_invite_token()
    token_h = hash_token(raw_token)

    invite = WorkspaceInvitation(
        workspace_id=workspace.id,
        invited_by_user_id=invited_by.id,
        token_hash=token_h,
        role=role,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
        max_uses=max_uses,
        uses=0,
    )
    db.session.add(invite)
    db.session.flush()  # assign invite.id before returning
    return invite, raw_token


def validate_invite(raw_token: str) -> str:
    """Return a status string for *raw_token* WITHOUT exposing workspace info.

    Does **not** reveal whether the workspace exists: callers should only
    show generic messages like "This invitation is invalid or expired".

    Returns
    -------
    str
        One of: ``"valid"``, ``"expired"``, ``"revoked"``, ``"used_up"``,
        ``"not_found"``.
    """
    token_h = hash_token(raw_token)
    invite = db.session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.token_hash == token_h
        )
    )
    if invite is None:
        return _NOT_FOUND
    if invite.is_revoked:
        return _REVOKED
    if invite.is_expired:
        return _EXPIRED
    if invite.is_used_up:
        return _USED_UP
    return _VALID


def get_invite_by_token(raw_token: str) -> WorkspaceInvitation | None:
    """Return the :class:`WorkspaceInvitation` for *raw_token*, or ``None``.

    Use :func:`validate_invite` to check its status before trusting this.
    """
    token_h = hash_token(raw_token)
    return db.session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.token_hash == token_h
        )
    )


def redeem_invite(raw_token: str, user: User) -> WorkspaceMember:
    """Redeem *raw_token* for *user* and return the resulting membership.

    Adds *user* to the workspace with the role stored on the invitation.
    Increments ``uses`` and sets ``accepted_at`` / ``accepted_by_user_id``.

    Idempotent:
        If *user* is already a member, their existing membership is returned
        and the invitation is marked accepted (if not yet) without creating
        a duplicate membership row.

    Parameters
    ----------
    raw_token:
        The raw (un-hashed) invite token submitted by the user.
    user:
        The authenticated user redeeming the token.

    Returns
    -------
    WorkspaceMember
        The new (or existing) membership.  Callers must commit after this
        call.

    Raises
    ------
    ValueError
        Status string as message: ``"not_found"``, ``"revoked"``,
        ``"expired"``, or ``"used_up"``.
    """
    token_h = hash_token(raw_token)
    invite = db.session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.token_hash == token_h
        )
    )

    if invite is None:
        raise ValueError(_NOT_FOUND)
    if invite.is_revoked:
        raise ValueError(_REVOKED)
    if invite.is_expired:
        raise ValueError(_EXPIRED)
    if invite.is_used_up:
        raise ValueError(_USED_UP)

    # Idempotency: user is already a member
    existing = db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == invite.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if existing is not None:
        # Mark accepted so the invite counter reflects reality, but don't
        # add another membership row or inflate uses beyond 1 per user.
        if invite.accepted_at is None:
            invite.accepted_at = datetime.now(UTC)
            invite.accepted_by_user_id = user.id
            invite.uses += 1
        return existing

    # Create the membership
    role_enum = WorkspaceMemberRole(invite.role)
    member = WorkspaceMember(
        workspace_id=invite.workspace_id,
        user_id=user.id,
        role=role_enum,
    )
    db.session.add(member)

    # Record redemption
    invite.uses += 1
    invite.accepted_at = datetime.now(UTC)
    invite.accepted_by_user_id = user.id

    return member


def revoke_invite(invite_id: int, actor: User) -> WorkspaceInvitation:
    """Revoke an invitation by *invite_id*.

    Parameters
    ----------
    invite_id:
        Primary key of the :class:`WorkspaceInvitation` to revoke.
    actor:
        The authenticated user performing the revocation.

    Returns
    -------
    WorkspaceInvitation
        The (now revoked) invitation.  Callers must commit after this call.

    Raises
    ------
    ValueError
        If no invitation with *invite_id* exists.
    PermissionError
        If *actor* is not the workspace owner or a site admin.
    """
    invite = db.session.get(WorkspaceInvitation, invite_id)
    if invite is None:
        raise ValueError(f"No invitation found with id={invite_id}")

    # Lazy-load the workspace for the permission check
    workspace = db.session.get(Workspace, invite.workspace_id)
    if workspace is None:
        raise ValueError("Workspace no longer exists")

    _require_owner_or_admin(workspace, actor, op_name="invite revocation")

    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
    return invite


def list_invites(
    workspace: Workspace,
    actor: User,
) -> list[WorkspaceInvitation]:
    """Return all invitations for *workspace*, newest first.

    Only workspace owners and site admins may list invitations.

    Raises
    ------
    PermissionError
        If *actor* is not an owner/admin.
    """
    _require_owner_or_admin(workspace, actor, op_name="invite listing")
    return list(
        db.session.scalars(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == workspace.id)
            .order_by(WorkspaceInvitation.created_at.desc())
        )
    )
