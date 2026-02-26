"""Comment CRUD request schemas."""

from __future__ import annotations

from marshmallow import fields, validate

from backend.schemas._base import BaseSchema


class CreateCommentSchema(BaseSchema):
    """POST /api/posts/<slug>/comments — add a comment or reply."""

    body = fields.Str(required=True, validate=validate.Length(min=1, max=10_000))
    parent_id = fields.Int(load_default=None, allow_none=True)


class UpdateCommentSchema(BaseSchema):
    """PUT /api/comments/<id> — edit comment body (author only)."""

    body = fields.Str(required=True, validate=validate.Length(min=1, max=10_000))
