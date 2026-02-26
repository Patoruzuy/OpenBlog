"""User profile request schemas."""

from __future__ import annotations

from marshmallow import fields, validate

from backend.schemas._base import BaseSchema


class UpdateProfileSchema(BaseSchema):
    """PATCH /api/users/<username> — update own profile.

    All fields are optional (partial update).  URL fields are plain strings
    here; ``validate_url()`` in the service layer enforces the http/https
    scheme requirement.
    """

    display_name = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=100),
    )
    bio = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=2000),
    )
    avatar_url = fields.Str(load_default=None, allow_none=True)
    website_url = fields.Str(load_default=None, allow_none=True)
    github_url = fields.Str(load_default=None, allow_none=True)
    tech_stack = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=200),
    )
    location = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=100),
    )
