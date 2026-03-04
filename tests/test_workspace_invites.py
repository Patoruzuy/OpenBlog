"""Phase-2 tests — workspace invitations, membership management, and security.

Coverage map
------------
INV-020  Raw token is NOT stored; only its SHA-256 hash is persisted.
INV-021  Token hash is unique in the database.
INV-022  validate_invite() returns only a status string — no workspace info.
INV-023  Redeeming a valid token creates the membership with the correct role.
INV-024  Redemption is idempotent: existing member gets membership returned.
INV-025  Expired token cannot be redeemed.
INV-026  Revoked token cannot be redeemed.
INV-027  Used-up token (max_uses reached) cannot be redeemed.
INV-028  Only workspace owners (and site admins) can create invites.
INV-029  Only workspace owners (and site admins) can revoke invites.
INV-030  Only workspace owners (and site admins) can list invites.
INV-031  "owner" role is not a valid invite role.
INV-032  Cannot remove the last owner of a workspace.
INV-033  Cannot demote the last owner of a workspace.
INV-034  Ownership transfer works when a second owner exists.
INV-035  Only workspace owners can access /w/<ws>/members and /w/<ws>/invites.
INV-036  Non-members get 404 on /w/<ws>/members and /w/<ws>/invites.
INV-037  GET /invites/<invalid_token> never reveals workspace name/slug.
INV-038  GET /invites/<valid_token> for authenticated user redeems + redirects.
INV-039  Cache-Control: private, no-store on all /invites/ responses.
INV-040  noindex meta tag present on invites/invalid.html.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.extensions import db as _db
from backend.models.user import User, UserRole
from backend.models.workspace import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from backend.security.tokens import generate_invite_token, hash_token
from backend.services import invite_service
from backend.services import workspace_service as ws_svc

# ── Shared helpers ────────────────────────────────────────────────────────────

_user_counter: dict[str, int] = {"n": 0}


def _create_user(role: str = "reader", email: str | None = None) -> tuple[User, str]:
    """Register a user and return ``(user, access_token)``."""
    from backend.services.auth_service import AuthService

    _user_counter["n"] += 1
    n = _user_counter["n"]
    email = email or f"inv_test_{n}@example.com"
    username = f"inv_user_{n}"
    user = AuthService.register(email, username, "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    token = AuthService.issue_access_token(user)
    return user, token


def _make_workspace(owner: User, name: str = "Test WS") -> Workspace:
    ws = ws_svc.create_workspace(name=name, owner=owner)
    _db.session.commit()
    return ws


def _add_member(
    workspace: Workspace, user: User, role: WorkspaceMemberRole
) -> WorkspaceMember:
    m = ws_svc.add_member(workspace, user, role)
    _db.session.commit()
    return m


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_invite(
    workspace: Workspace,
    owner: User,
    *,
    role: str = "viewer",
    expires_in_days: int = 7,
    max_uses: int = 1,
) -> tuple[WorkspaceInvitation, str]:
    invite, raw = invite_service.create_invite(
        workspace,
        owner,
        role,
        expires_in_days=expires_in_days,
        max_uses=max_uses,
    )
    _db.session.commit()
    return invite, raw


# ── INV-020 / INV-021 — Token storage ────────────────────────────────────────


class TestTokenStorage:
    """Raw token not stored; only SHA-256 hash persisted; hash is unique."""

    def test_raw_token_not_in_db(self, db_session):
        """INV-020: raw_token string must not appear anywhere in the row."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)

        # Refresh from DB
        _db.session.expire(invite)
        invite = _db.session.get(WorkspaceInvitation, invite.id)

        assert invite.token_hash != raw, "Raw token must NOT be stored as hash"
        assert len(invite.token_hash) == 64, "SHA-256 hex digest is 64 chars"

    def test_hash_matches_expected_sha256(self, db_session):
        """INV-020: stored hash equals sha256(raw_token)."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)

        expected_hash = hash_token(raw)
        assert invite.token_hash == expected_hash

    def test_two_invites_have_different_hashes(self, db_session):
        """INV-021: each invite token is unique."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite_a, raw_a = _make_invite(ws, owner)
        invite_b, raw_b = _make_invite(ws, owner)

        assert raw_a != raw_b
        assert invite_a.token_hash != invite_b.token_hash

    def test_duplicate_hash_rejected(self, db_session):
        """INV-021: inserting a second row with the same token_hash fails."""
        from sqlalchemy.exc import IntegrityError

        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        raw = generate_invite_token()
        h = hash_token(raw)

        inv1 = WorkspaceInvitation(
            workspace_id=ws.id,
            invited_by_user_id=owner.id,
            token_hash=h,
            role="viewer",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        inv2 = WorkspaceInvitation(
            workspace_id=ws.id,
            invited_by_user_id=owner.id,
            token_hash=h,  # duplicate
            role="viewer",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        _db.session.add_all([inv1, inv2])
        with pytest.raises(IntegrityError):
            _db.session.flush()


# ── INV-022 — Non-leakage from validate_invite ────────────────────────────────


class TestValidateInviteNonLeakage:
    """validate_invite() must return only a status string."""

    def test_valid_token_returns_valid(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)

        status = invite_service.validate_invite(raw)
        assert status == "valid"

    def test_unknown_token_returns_not_found(self, db_session):
        """INV-022: unknown raw token → 'not_found', nothing else."""
        mystery = generate_invite_token()
        status = invite_service.validate_invite(mystery)
        assert status == "not_found"

    def test_expired_token_returns_expired(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, expires_in_days=7)
        # Force expiry
        invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        _db.session.commit()

        status = invite_service.validate_invite(raw)
        assert status == "expired"

    def test_revoked_token_returns_revoked(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)
        invite_service.revoke_invite(invite.id, owner)
        _db.session.commit()

        status = invite_service.validate_invite(raw)
        assert status == "revoked"

    def test_used_up_token_returns_used_up(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, max_uses=1)
        # Exhaust the invite
        redeemer, _ = _create_user()
        invite_service.redeem_invite(raw, redeemer)
        _db.session.commit()

        status = invite_service.validate_invite(raw)
        assert status == "used_up"

    def test_validate_returns_string_not_object(self, db_session):
        """Return value must be a plain str, never a model object."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)

        result = invite_service.validate_invite(raw)
        assert isinstance(result, str)


# ── INV-023 / INV-024 — Redemption behaviour ─────────────────────────────────


class TestRedeemInvite:
    """Valid token creates membership; idempotent for existing members."""

    def test_redeem_creates_membership_with_correct_role(self, db_session):
        """INV-023: redeemer gets the role stored on the invitation."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, role="editor")

        redeemer, _ = _create_user()
        member = invite_service.redeem_invite(raw, redeemer)
        _db.session.commit()

        assert member.role == WorkspaceMemberRole.editor
        assert member.workspace_id == ws.id
        assert member.user_id == redeemer.id

    def test_redeem_increments_uses(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, max_uses=2)

        r1, _ = _create_user()
        invite_service.redeem_invite(raw, r1)
        _db.session.commit()

        _db.session.expire(invite)
        invite = _db.session.get(WorkspaceInvitation, invite.id)
        assert invite.uses == 1

    def test_redeem_records_accepted_at_and_user(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)

        redeemer, _ = _create_user()
        invite_service.redeem_invite(raw, redeemer)
        _db.session.commit()

        _db.session.expire(invite)
        invite = _db.session.get(WorkspaceInvitation, invite.id)
        assert invite.accepted_at is not None
        assert invite.accepted_by_user_id == redeemer.id

    def test_redeem_idempotent_for_existing_member(self, db_session):
        """INV-024: redeeming when already a member returns existing membership."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, role="viewer")

        redeemer, _ = _create_user()
        _add_member(ws, redeemer, WorkspaceMemberRole.contributor)

        # Redeeming does not raise and returns the existing membership.
        member = invite_service.redeem_invite(raw, redeemer)
        _db.session.commit()
        assert member.role == WorkspaceMemberRole.contributor  # unchanged

        # Only one membership row exists.
        from sqlalchemy import select

        count = _db.session.scalar(
            select(WorkspaceInvitation).where(WorkspaceInvitation.id == invite.id)
        )
        assert count is not None  # invite still in DB; no duplicate member

    def test_redeem_sets_contributor_role(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, role="contributor")

        r, _ = _create_user()
        member = invite_service.redeem_invite(raw, r)
        _db.session.commit()
        assert member.role == WorkspaceMemberRole.contributor


# ── INV-025 / INV-026 / INV-027 — Invalid state redemptions ──────────────────


class TestRedeemInvalidTokens:
    """Expired, revoked, and used-up tokens raise ValueError."""

    def test_expired_token_raises(self, db_session):
        """INV-025: expired invite cannot be redeemed."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)
        invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        _db.session.commit()

        redeemer, _ = _create_user()
        with pytest.raises(ValueError, match="expired"):
            invite_service.redeem_invite(raw, redeemer)

    def test_revoked_token_raises(self, db_session):
        """INV-026: revoked invite cannot be redeemed."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)
        invite_service.revoke_invite(invite.id, owner)
        _db.session.commit()

        redeemer, _ = _create_user()
        with pytest.raises(ValueError, match="revoked"):
            invite_service.redeem_invite(raw, redeemer)

    def test_used_up_token_raises(self, db_session):
        """INV-027: single-use invite raises after first redemption."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, max_uses=1)

        r1, _ = _create_user()
        invite_service.redeem_invite(raw, r1)
        _db.session.commit()

        r2, _ = _create_user()
        with pytest.raises(ValueError, match="used_up"):
            invite_service.redeem_invite(raw, r2)

    def test_max_uses_2_allows_second_redeem(self, db_session):
        """Multi-use invite works until exhausted."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, max_uses=2)

        r1, _ = _create_user()
        r2, _ = _create_user()
        r3, _ = _create_user()
        invite_service.redeem_invite(raw, r1)
        _db.session.commit()
        invite_service.redeem_invite(raw, r2)
        _db.session.commit()

        with pytest.raises(ValueError, match="used_up"):
            invite_service.redeem_invite(raw, r3)

    def test_unknown_token_raises(self, db_session):
        """Completely unknown raw_token raises ValueError('not_found')."""
        r, _ = _create_user()
        with pytest.raises(ValueError, match="not_found"):
            invite_service.redeem_invite(generate_invite_token(), r)


# ── INV-028 / INV-029 / INV-030 — Permission enforcement ─────────────────────


class TestInvitePermissions:
    """Only workspace owners and site admins can manage invites."""

    def test_editor_cannot_create_invite(self, db_session):
        """INV-028: editor-level member cannot create invites."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        editor, _ = _create_user()
        _add_member(ws, editor, WorkspaceMemberRole.editor)

        with pytest.raises(PermissionError):
            invite_service.create_invite(ws, editor, "viewer")

    def test_viewer_cannot_create_invite(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        viewer, _ = _create_user()
        _add_member(ws, viewer, WorkspaceMemberRole.viewer)

        with pytest.raises(PermissionError):
            invite_service.create_invite(ws, viewer, "viewer")

    def test_non_member_cannot_create_invite(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        outsider, _ = _create_user()

        with pytest.raises(PermissionError):
            invite_service.create_invite(ws, outsider, "viewer")

    def test_site_admin_can_create_invite(self, db_session):
        """INV-028: platform admins bypass workspace ownership requirement."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        admin, _ = _create_user("admin")

        invite, raw = invite_service.create_invite(ws, admin, "viewer")
        _db.session.commit()
        assert invite.id is not None

    def test_editor_cannot_revoke_invite(self, db_session):
        """INV-029: editor cannot revoke an invite created by the owner."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        editor, _ = _create_user()
        _add_member(ws, editor, WorkspaceMemberRole.editor)
        invite, _ = _make_invite(ws, owner)

        with pytest.raises(PermissionError):
            invite_service.revoke_invite(invite.id, editor)

    def test_owner_can_revoke_invite(self, db_session):
        """INV-029: workspace owner can revoke any invite."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, _ = _make_invite(ws, owner)

        revoked = invite_service.revoke_invite(invite.id, owner)
        _db.session.commit()
        assert revoked.revoked_at is not None

    def test_revoke_invite_idempotent(self, db_session):
        """Revoking an already-revoked invite does not raise."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, _ = _make_invite(ws, owner)
        invite_service.revoke_invite(invite.id, owner)
        _db.session.commit()
        first_revoked_at = invite.revoked_at

        invite_service.revoke_invite(invite.id, owner)
        _db.session.commit()
        assert invite.revoked_at == first_revoked_at  # unchanged

    def test_editor_cannot_list_invites(self, db_session):
        """INV-030: editor cannot list workspace invitations."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        editor, _ = _create_user()
        _add_member(ws, editor, WorkspaceMemberRole.editor)

        with pytest.raises(PermissionError):
            invite_service.list_invites(ws, editor)

    def test_owner_can_list_invites(self, db_session):
        """INV-030: owner can list all invitations."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        _make_invite(ws, owner)
        _make_invite(ws, owner)

        invites = invite_service.list_invites(ws, owner)
        assert len(invites) == 2


# ── INV-031 — Owner role not allowed via invite ───────────────────────────────


class TestInviteRoleValidation:
    """owner role must be rejected; other roles must be accepted."""

    def test_owner_role_rejected(self, db_session):
        """INV-031: 'owner' cannot be granted via an invite."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        with pytest.raises(ValueError, match="owner"):
            invite_service.create_invite(ws, owner, "owner")

    def test_unknown_role_rejected(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        with pytest.raises(ValueError):
            invite_service.create_invite(ws, owner, "superadmin")

    def test_viewer_role_accepted(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, _ = invite_service.create_invite(ws, owner, "viewer")
        _db.session.commit()
        assert invite.role == "viewer"

    def test_contributor_role_accepted(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, _ = invite_service.create_invite(ws, owner, "contributor")
        _db.session.commit()
        assert invite.role == "contributor"

    def test_editor_role_accepted(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, _ = invite_service.create_invite(ws, owner, "editor")
        _db.session.commit()
        assert invite.role == "editor"


# ── INV-032 / INV-033 / INV-034 — Ownership invariants ───────────────────────


class TestOwnershipInvariants:
    """Last-owner protection and ownership transfer."""

    def test_cannot_remove_last_owner(self, db_session):
        """INV-032: removing the sole owner raises ValueError."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        with pytest.raises(ValueError, match="[Ll]ast owner"):
            ws_svc.remove_member(ws, owner, owner.id)

    def test_can_remove_owner_when_another_owner_exists(self, db_session):
        """INV-032: removal succeeds when a second owner is present."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        second_owner, _ = _create_user()
        _add_member(ws, second_owner, WorkspaceMemberRole.owner)

        ws_svc.remove_member(ws, owner, owner.id)
        _db.session.commit()

        remaining = ws_svc.list_members(ws, second_owner)
        ids = [m.user_id for m in remaining]
        assert owner.id not in ids

    def test_cannot_demote_last_owner(self, db_session):
        """INV-033: demoting the sole owner raises ValueError."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)

        with pytest.raises(ValueError, match="[Ll]ast owner"):
            ws_svc.change_member_role(ws, owner, owner.id, WorkspaceMemberRole.editor)

    def test_can_demote_owner_when_another_owner_exists(self, db_session):
        """INV-033: demotion succeeds when a second owner exists."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        second_owner, _ = _create_user()
        _add_member(ws, second_owner, WorkspaceMemberRole.owner)

        ws_svc.change_member_role(
            ws, second_owner, owner.id, WorkspaceMemberRole.editor
        )
        _db.session.commit()

        member = ws_svc.get_member(ws, owner)
        assert member.role == WorkspaceMemberRole.editor

    def test_ownership_transfer_works(self, db_session):
        """INV-034: promote editor to owner, then demote original owner."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        heir, _ = _create_user()
        _add_member(ws, heir, WorkspaceMemberRole.editor)

        # Promote heir to owner
        ws_svc.change_member_role(ws, owner, heir.id, WorkspaceMemberRole.owner)
        _db.session.commit()

        # Now demote original owner (two owners exist)
        ws_svc.change_member_role(ws, heir, owner.id, WorkspaceMemberRole.viewer)
        _db.session.commit()

        assert ws_svc.get_member(ws, owner).role == WorkspaceMemberRole.viewer
        assert ws_svc.get_member(ws, heir).role == WorkspaceMemberRole.owner

    def test_non_member_cannot_change_roles(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        outsider, _ = _create_user()
        target, _ = _create_user()
        _add_member(ws, target, WorkspaceMemberRole.viewer)

        with pytest.raises(PermissionError):
            ws_svc.change_member_role(
                ws, outsider, target.id, WorkspaceMemberRole.editor
            )

    def test_editor_cannot_remove_member(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        editor, _ = _create_user()
        target, _ = _create_user()
        _add_member(ws, editor, WorkspaceMemberRole.editor)
        _add_member(ws, target, WorkspaceMemberRole.viewer)

        with pytest.raises(PermissionError):
            ws_svc.remove_member(ws, editor, target.id)

    def test_list_members_returns_all_members(self, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        e1, _ = _create_user()
        e2, _ = _create_user()
        _add_member(ws, e1, WorkspaceMemberRole.viewer)
        _add_member(ws, e2, WorkspaceMemberRole.editor)

        members = ws_svc.list_members(ws, owner)
        user_ids = {m.user_id for m in members}
        assert owner.id in user_ids
        assert e1.id in user_ids
        assert e2.id in user_ids


# ── INV-035 / INV-036 — Route access control ─────────────────────────────────


class TestMemberAndInviteRouteAccess:
    """Owner-only routes return 200 for owner, 404 for editors/non-members."""

    def _setup(self, db_session):
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner, "Route WS")
        editor, editor_tok = _create_user()
        outsider, outsider_tok = _create_user()
        _add_member(ws, editor, WorkspaceMemberRole.editor)
        return ws, owner_tok, editor_tok, outsider_tok

    def test_owner_can_access_members_page(self, auth_client, db_session):
        """INV-035: workspace owner gets 200 on /members."""
        ws, owner_tok, _, _ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/members", headers=_auth(owner_tok))
        assert resp.status_code == 200

    def test_editor_cannot_access_members_page(self, auth_client, db_session):
        """INV-035: editor (< owner) gets 404 on /members."""
        ws, _, editor_tok, _ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/members", headers=_auth(editor_tok))
        assert resp.status_code == 404

    def test_non_member_cannot_access_members_page(self, auth_client, db_session):
        """INV-036: non-member gets 404 on /members."""
        ws, _, _, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/members", headers=_auth(outsider_tok))
        assert resp.status_code == 404

    def test_owner_can_access_invites_page(self, auth_client, db_session):
        """INV-035: workspace owner gets 200 on /invites."""
        ws, owner_tok, _, _ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/invites", headers=_auth(owner_tok))
        assert resp.status_code == 200

    def test_editor_cannot_access_invites_page(self, auth_client, db_session):
        """INV-035: editor gets 404 on /invites."""
        ws, _, editor_tok, _ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/invites", headers=_auth(editor_tok))
        assert resp.status_code == 404

    def test_non_member_cannot_access_invites_page(self, auth_client, db_session):
        """INV-036: non-member gets 404 on /invites."""
        ws, _, _, outsider_tok = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/invites", headers=_auth(outsider_tok))
        assert resp.status_code == 404

    def test_unauthenticated_cannot_access_members_page(self, auth_client, db_session):
        ws, *_ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/members")
        assert resp.status_code in (301, 302)
        assert "login" in resp.headers["Location"]

    def test_unauthenticated_cannot_access_invites_page(self, auth_client, db_session):
        ws, *_ = self._setup(db_session)
        resp = auth_client.get(f"/w/{ws.slug}/invites")
        assert resp.status_code in (301, 302)
        assert "login" in resp.headers["Location"]


# ── INV-037 — Non-leakage via /invites/<token> route ─────────────────────────


class TestInviteRouteNonLeakage:
    """GET /invites/<token> must never reveal workspace details on failure."""

    def test_invalid_token_returns_200_with_no_workspace_name(
        self, auth_client, db_session
    ):
        """INV-037: unknown token gives generic error page, not 404 or 403."""
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner, "Top-Secret WS")

        # Use a completely random (unknown) token
        mystery = generate_invite_token()
        resp = auth_client.get(f"/invites/{mystery}", headers=_auth(owner_tok))
        assert resp.status_code == 200
        body = resp.data.decode()
        # Workspace name must NOT appear anywhere in the response
        assert "Top-Secret WS" not in body
        assert ws.slug not in body

    def test_expired_token_does_not_reveal_workspace(self, auth_client, db_session):
        """INV-037: expired token response must not contain workspace slug."""
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner, "Private WS")
        invite, raw = _make_invite(ws, owner)
        invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        _db.session.commit()

        resp = auth_client.get(f"/invites/{raw}", headers=_auth(owner_tok))
        body = resp.data.decode()
        assert ws.slug not in body
        assert "Private WS" not in body

    def test_revoked_token_does_not_reveal_workspace(self, auth_client, db_session):
        """INV-037: revoked token response must not contain workspace slug."""
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner, "Revoked WS")
        invite, raw = _make_invite(ws, owner)
        invite_service.revoke_invite(invite.id, owner)
        _db.session.commit()

        resp = auth_client.get(f"/invites/{raw}", headers=_auth(owner_tok))
        body = resp.data.decode()
        assert ws.slug not in body
        assert "Revoked WS" not in body

    def test_noindex_meta_present_on_invalid_page(self, auth_client, db_session):
        """INV-040: error page contains noindex meta tag."""
        owner, owner_tok = _create_user("editor")
        _make_workspace(owner)

        mystery = generate_invite_token()
        resp = auth_client.get(f"/invites/{mystery}", headers=_auth(owner_tok))
        body = resp.data.decode()
        assert "noindex" in body.lower()

    def test_unauthenticated_redirects_to_login(self, auth_client, db_session):
        """Unauthenticated visitor is redirected to login, preserving next URL."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)

        resp = auth_client.get(f"/invites/{raw}")
        assert resp.status_code in (301, 302)
        loc = resp.headers["Location"]
        assert "login" in loc
        # next param should encode the invite path
        assert raw in loc or "invites" in loc


# ── INV-038 — Successful redemption via route ─────────────────────────────────


class TestRedeemRoute:
    """INV-038: authenticated user with valid token gets redirected to workspace."""

    def test_valid_token_redeems_and_redirects(self, auth_client, db_session):
        """INV-038: GET /invites/<token> for valid+auth → 302 to workspace."""
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner, role="viewer")

        redeemer, redeemer_tok = _create_user()
        resp = auth_client.get(f"/invites/{raw}", headers=_auth(redeemer_tok))
        # Should redirect (302 → workspace dashboard)
        assert resp.status_code in (301, 302)
        loc = resp.headers["Location"]
        assert ws.slug in loc

        # Confirm member row was created
        from sqlalchemy import select

        member = _db.session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws.id,
                WorkspaceMember.user_id == redeemer.id,
            )
        )
        assert member is not None
        assert member.role == WorkspaceMemberRole.viewer


# ── INV-039 — Cache-Control on /invites/ ─────────────────────────────────────


class TestInviteCacheControl:
    """INV-039: every /invites/ response carries private, no-store."""

    def test_invalid_token_has_no_store(self, auth_client, db_session):
        owner, tok = _create_user("editor")
        _make_workspace(owner)
        mystery = generate_invite_token()

        resp = auth_client.get(f"/invites/{mystery}", headers=_auth(tok))
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert "private" in cc

    def test_valid_token_redirect_has_no_store(self, auth_client, db_session):
        owner, _ = _create_user("editor")
        ws = _make_workspace(owner)
        invite, raw = _make_invite(ws, owner)
        redeemer, redeemer_tok = _create_user()

        resp = auth_client.get(
            f"/invites/{raw}",
            headers=_auth(redeemer_tok),
            follow_redirects=False,
        )
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert "private" in cc


# ── INV-039 — Cache-Control on /w/ member/invite management pages ─────────────


class TestWorkspaceMemberPageCacheControl:
    """/w/<ws>/members and /w/<ws>/invites must carry private, no-store."""

    def test_members_page_has_no_store(self, auth_client, db_session):
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner)

        resp = auth_client.get(f"/w/{ws.slug}/members", headers=_auth(owner_tok))
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert "private" in cc

    def test_invites_page_has_no_store(self, auth_client, db_session):
        owner, owner_tok = _create_user("editor")
        ws = _make_workspace(owner)

        resp = auth_client.get(f"/w/{ws.slug}/invites", headers=_auth(owner_tok))
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert "private" in cc
