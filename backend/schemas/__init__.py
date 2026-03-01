"""Request validation schemas.

All schemas extend :class:`BaseSchema` (unknown fields are silently excluded).
Use :func:`load_json` in route handlers to validate and deserialise request
body in a single call.

Example::

    from backend.schemas import CreatePostSchema, load_json

    @bp.post("/")
    def create_post():
        data, err = load_json(CreatePostSchema())
        if err:
            return err
        ...
"""

from backend.schemas._base import BaseSchema, load_json
from backend.schemas.auth import (
    LoginSchema,
    LogoutSchema,
    RefreshSchema,
    RegisterSchema,
)
from backend.schemas.comment import CreateCommentSchema, UpdateCommentSchema
from backend.schemas.post import CreatePostSchema, PublishPostSchema, UpdatePostSchema
from backend.schemas.revision import RejectRevisionSchema, SubmitRevisionSchema
from backend.schemas.user import UpdateProfileSchema

__all__ = [
    "BaseSchema",
    "load_json",
    # auth
    "RegisterSchema",
    "LoginSchema",
    "RefreshSchema",
    "LogoutSchema",
    # posts
    "CreatePostSchema",
    "UpdatePostSchema",
    "PublishPostSchema",
    # users
    "UpdateProfileSchema",
    # revisions
    "SubmitRevisionSchema",
    "RejectRevisionSchema",
    # comments
    "CreateCommentSchema",
    "UpdateCommentSchema",
]
