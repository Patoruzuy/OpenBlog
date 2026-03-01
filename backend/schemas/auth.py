"""Authentication request schemas."""

from __future__ import annotations

from marshmallow import fields, validate

from backend.schemas._base import BaseSchema


class RegisterSchema(BaseSchema):
    """POST /api/auth/register — create a new account."""

    email = fields.Email(required=True)
    username = fields.Str(required=True, validate=validate.Length(min=2, max=50))
    # Password length is enforced as a business rule in AuthService, not here.
    password = fields.Str(required=True)
    display_name = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=100)
    )


class LoginSchema(BaseSchema):
    """POST /api/auth/login — authenticate and receive tokens."""

    email = fields.Str(required=True, validate=validate.Length(min=1))
    password = fields.Str(required=True)


class RefreshSchema(BaseSchema):
    """POST /api/auth/refresh — rotate the refresh token."""

    refresh_token = fields.Str(required=True, validate=validate.Length(min=1))


class LogoutSchema(BaseSchema):
    """POST /api/auth/logout — revoke a refresh token."""

    refresh_token = fields.Str(required=True, validate=validate.Length(min=1))
