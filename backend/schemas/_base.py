"""Shared base schema and JSON-loading helper."""

from __future__ import annotations

from flask import jsonify, request
from marshmallow import EXCLUDE, Schema, ValidationError


class BaseSchema(Schema):
    """Base schema that silently ignores unknown fields.

    All request schemas inherit from this so that clients can send extra
    fields (e.g. future keys) without triggering validation errors.
    """

    class Meta:
        unknown = EXCLUDE


def load_json(schema: Schema, body: dict | None = None):
    """Validate ``request.get_json()`` (or *body*) against *schema*.

    Returns ``(data, None)`` on success or ``(None, error_response)`` on
    validation failure, allowing callers to do::

        data, err = load_json(MySchema())
        if err:
            return err
        # use data...

    Parameters
    ----------
    schema:
        An instantiated marshmallow schema.
    body:
        Pre-parsed dict.  When ``None`` the function calls
        ``request.get_json(silent=True)`` automatically.
    """
    raw = body if body is not None else (request.get_json(silent=True) or {})
    try:
        return schema.load(raw), None
    except ValidationError as exc:
        return None, (
            jsonify({"error": "Validation failed.", "details": exc.messages}),
            400,
        )
