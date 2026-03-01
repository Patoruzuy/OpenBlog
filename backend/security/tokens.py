"""Secure token generation and hashing for workspace invitations.

Strategy
--------
Raw tokens are cryptographically random URL-safe strings (32+ bytes = 256 bits
of entropy).  They are **never** persisted to the database.  Only the
SHA-256 hex digest of the raw token is stored, so a DB leak cannot be used
to redeem an invitation without brute-force preimage search.

Usage
-----
At invite creation::

    raw_token = generate_invite_token()
    invite.token_hash = hash_token(raw_token)
    # Show raw_token to the inviter ONCE, then discard it.

At redemption::

    invite = db.session.scalar(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.token_hash == hash_token(submitted_token))
    )

Token URL format: ``/invites/<raw_token>``
"""
from __future__ import annotations

import hashlib
import secrets

# 32 bytes → 256-bit entropy; urlsafe base64 → ~43 character string
_TOKEN_BYTES: int = 32


def generate_invite_token() -> str:
    """Return a cryptographically random URL-safe token string.

    Uses :func:`secrets.token_urlsafe` which is backed by ``os.urandom``
    and is safe for security-sensitive tokens.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    """Return a stable SHA-256 hex digest of *raw_token*.

    The returned value is 64 hex characters (256 bits).  Storing only this
    digest means the raw token cannot be recovered from the database.

    Parameters
    ----------
    raw_token:
        The plaintext token returned by :func:`generate_invite_token`.

    Returns
    -------
    str
        64-character lowercase hex string.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
