"""Database model package.

Importing this package guarantees that all SQLAlchemy model classes are
registered with the metadata — which is required for:

  - Alembic autogenerate to detect all tables.
  - ``db.create_all()`` to work in tests (TestingConfig / SQLite in-memory).

Import order matters: models with foreign keys must be imported after the
models they reference.  The ordering below satisfies all FK dependencies.
"""

from backend.models.analytics import AnalyticsEvent
from backend.models.badge import Badge, UserBadge
from backend.models.bookmark import Bookmark
from backend.models.comment import Comment
from backend.models.follow import Follow
from backend.models.notification import Notification
from backend.models.post import Post
from backend.models.post_version import PostVersion
from backend.models.revision import Revision
from backend.models.tag import PostTag, Tag
from backend.models.user import User
from backend.models.vote import Vote

__all__ = [
    "AnalyticsEvent",
    "Badge",
    "Bookmark",
    "Comment",
    "Follow",
    "Notification",
    "Post",
    "PostTag",
    "PostVersion",
    "Revision",
    "Tag",
    "User",
    "UserBadge",
    "Vote",
]
