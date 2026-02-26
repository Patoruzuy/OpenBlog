"""Post CRUD request schemas."""

from __future__ import annotations

from marshmallow import fields, validate

from backend.schemas._base import BaseSchema


class CreatePostSchema(BaseSchema):
    """POST /api/posts/ — create a new draft post."""

    title = fields.Str(required=True, validate=validate.Length(min=1, max=500))
    markdown_body = fields.Str(load_default="")
    tags = fields.List(fields.Str(), load_default=None, allow_none=True)
    seo_title = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    seo_description = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))
    # og_image_url: URL format is enforced by the service; schema just coerces to Str
    og_image_url = fields.Str(load_default=None, allow_none=True)


class UpdatePostSchema(BaseSchema):
    """PUT /api/posts/<slug> — partial post update (all fields optional)."""

    title = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=500))
    markdown_body = fields.Str(load_default=None, allow_none=True)
    tags = fields.List(fields.Str(), load_default=None, allow_none=True)
    seo_title = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    seo_description = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))
    og_image_url = fields.Str(load_default=None, allow_none=True)


class PublishPostSchema(BaseSchema):
    """POST /api/posts/<slug>/publish — publish or schedule a post.

    ``publish_at`` is kept as a raw string so the route can delegate datetime
    parsing to Python's ``datetime.fromisoformat()``, preserving the existing
    error message.
    """

    publish_at = fields.Str(load_default=None, allow_none=True)
