"""Revision workflow request schemas."""

from __future__ import annotations

from marshmallow import fields, validate

from backend.schemas._base import BaseSchema


class SubmitRevisionSchema(BaseSchema):
    """POST /api/posts/<slug>/revisions — propose a change to a post."""

    proposed_markdown = fields.Str(
        required=True,
        validate=validate.Length(min=1),
    )
    summary = fields.Str(
        load_default="",
        validate=validate.Length(max=1000),
    )


class RejectRevisionSchema(BaseSchema):
    """POST /api/revisions/<id>/reject — reject with an optional note."""

    note = fields.Str(
        load_default="",
        validate=validate.Length(max=2000),
    )
