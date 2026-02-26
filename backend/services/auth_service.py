"""Authentication service.

Provides stateless JWT issuance/verification and refresh-token rotation.
Passwords are hashed with argon2-cffi.

Refresh tokens are stored in Redis so that logout (revocation) takes effect
immediately server-side without needing to wait for token expiry.

Redis key schema
----------------
  rt:{jti}  →  str(user_id)   TTL = REFRESH_TOKEN_EXPIRY seconds
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import current_app
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.user import User, UserRole
from backend.utils import metrics

_ph = PasswordHasher()


class AuthError(Exception):
    """Raised for any authentication or authorisation failure.

    ``status_code`` maps to an HTTP status so routes can return it directly.
    """

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthService:
    # ── Registration ──────────────────────────────────────────────────────────

    @staticmethod
    def register(
        email: str,
        username: str,
        password: str,
        display_name: str | None = None,
    ) -> User:
        """Create a new user with role=reader.

        Raises
        ------
        AuthError(400)  password too short (< 8 chars).
        AuthError(409)  email or username already taken.
        """
        if len(password) < 8:
            raise AuthError("Password must be at least 8 characters.", 400)

        user = User(
            email=email.lower().strip(),
            username=username.strip(),
            display_name=display_name or username.strip(),
            password_hash=_ph.hash(password),
            role=UserRole.reader,
        )
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise AuthError("Email or username is already taken.", 409)
        metrics.user_registrations.inc()
        return user

    # ── Login ─────────────────────────────────────────────────────────────────

    @staticmethod
    def login(email: str, password: str) -> tuple[User, str, str]:
        """Verify credentials and return ``(user, access_token, refresh_token)``.

        Raises
        ------
        AuthError(401)  invalid credentials or deactivated account.
        """
        user = db.session.query(User).filter_by(email=email.lower().strip()).first()
        if user is None:
            metrics.user_logins.labels(outcome="failure").inc()
            raise AuthError("Invalid email or password.")
        if not user.is_active:
            metrics.user_logins.labels(outcome="failure").inc()
            raise AuthError("Account is deactivated.")
        if not user.password_hash:
            # OAuth-only account — no local password set.
            raise AuthError("Invalid email or password.")
        try:
            _ph.verify(user.password_hash, password)
        except VerifyMismatchError:
            metrics.user_logins.labels(outcome="failure").inc()
            raise AuthError("Invalid email or password.")

        # Rehash transparently if argon2 parameters have been strengthened.
        if _ph.check_needs_rehash(user.password_hash):
            user.password_hash = _ph.hash(password)
            db.session.commit()

        access_token, refresh_token = AuthService.issue_tokens(user)
        metrics.user_logins.labels(outcome="success").inc()
        return user, access_token, refresh_token

    # ── Token issuance ────────────────────────────────────────────────────────

    @staticmethod
    def issue_tokens(user: User) -> tuple[str, str]:
        """Issue a fresh ``(access_token, refresh_token)`` pair."""
        return AuthService.issue_access_token(user), AuthService.issue_refresh_token(user)

    @staticmethod
    def issue_access_token(user: User) -> str:
        """Create a signed, short-lived access JWT."""
        now = datetime.now(UTC)
        expiry: int = int(current_app.config.get("ACCESS_TOKEN_EXPIRY", 900))
        payload = {
            "sub": str(user.id),
            "role": user.role.value,
            "type": "access",
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + timedelta(seconds=expiry),
        }
        return jwt.encode(
            payload,
            current_app.config["JWT_SECRET_KEY"],
            algorithm="HS256",
        )

    @staticmethod
    def issue_refresh_token(user: User) -> str:
        """Create a signed refresh JWT and persist its JTI to Redis."""
        now = datetime.now(UTC)
        expiry: int = int(current_app.config.get("REFRESH_TOKEN_EXPIRY", 604800))
        jti = uuid.uuid4().hex
        payload = {
            "sub": str(user.id),
            "type": "refresh",
            "jti": jti,
            "iat": now,
            "exp": now + timedelta(seconds=expiry),
        }
        token = jwt.encode(
            payload,
            current_app.config["JWT_SECRET_KEY"],
            algorithm="HS256",
        )
        # Store JTI in Redis so it can be revoked before expiry.
        redis_client = current_app.extensions["redis"]
        redis_client.setex(f"rt:{jti}", expiry, str(user.id))
        return token

    # ── Token verification ────────────────────────────────────────────────────

    @staticmethod
    def verify_access_token(token: str) -> dict:
        """Decode and validate an access JWT.

        Raises ``AuthError`` on any failure (expired, malformed, wrong type).
        """
        try:
            payload = jwt.decode(
                token,
                current_app.config["JWT_SECRET_KEY"],
                algorithms=["HS256"],
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("Access token has expired.")
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"Invalid access token: {exc}")
        if payload.get("type") != "access":
            raise AuthError("Token is not an access token.")
        return payload

    @staticmethod
    def verify_refresh_token(token: str) -> dict:
        """Decode and validate a refresh JWT, checking Redis presence.

        Raises ``AuthError`` if the token is expired, invalid, or revoked.
        """
        try:
            payload = jwt.decode(
                token,
                current_app.config["JWT_SECRET_KEY"],
                algorithms=["HS256"],
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("Refresh token has expired.")
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"Invalid refresh token: {exc}")
        if payload.get("type") != "refresh":
            raise AuthError("Token is not a refresh token.")
        jti = payload["jti"]
        redis_client = current_app.extensions["redis"]
        if not redis_client.exists(f"rt:{jti}"):
            raise AuthError("Refresh token has been revoked or does not exist.")
        return payload

    # ── Token rotation ────────────────────────────────────────────────────────

    @staticmethod
    def rotate_refresh_token(token: str) -> tuple[str, str]:
        """Validate ``token``, revoke it, and issue a fresh pair.

        Implements single-use refresh tokens: the old token is deleted from
        Redis before the new pair is issued.  A second call with the same
        token will fail because it is no longer in Redis.

        Raises ``AuthError`` on any validation failure.
        """
        payload = AuthService.verify_refresh_token(token)
        AuthService.revoke_refresh_token(payload["jti"])
        user = db.session.get(User, int(payload["sub"]))
        if user is None or not user.is_active:
            raise AuthError("User not found or deactivated.")
        return AuthService.issue_tokens(user)

    # ── Revocation ────────────────────────────────────────────────────────────

    @staticmethod
    def revoke_refresh_token(jti: str) -> None:
        """Remove the JTI from Redis (immediate server-side logout)."""
        redis_client = current_app.extensions["redis"]
        redis_client.delete(f"rt:{jti}")
