"""Database model package.

Importing this package guarantees that all SQLAlchemy model classes are
registered with the metadata — which is required for:

  - Alembic autogenerate to detect all tables.
  - ``db.create_all()`` to work in tests (TestingConfig / SQLite in-memory).

Import order matters: models with foreign keys must be imported after the
models they reference.  The ordering below satisfies all FK dependencies.
"""

from backend.models.ab_experiment import ABExperiment, ABExperimentRun, ABExperimentStatus
from backend.models.admin import AuditLog, SiteSetting
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.ai_review import (
    AIProvider,
    AIReviewRequest,
    AIReviewResult,
    AIReviewStatus,
    AIReviewType,
)
from backend.models.analytics import AnalyticsEvent
from backend.models.badge import Badge, UserBadge
from backend.models.bookmark import Bookmark
from backend.models.comment import Comment
from backend.models.comment_attachment import CommentAttachment
from backend.models.content_link import VALID_LINK_TYPES, ContentLink
from backend.models.digest_run import DigestRun
from backend.models.email_delivery_log import EmailDeliveryLog
from backend.models.follow import Follow
from backend.models.newsletter import NewsletterSubscription
from backend.models.notification import Notification
from backend.models.notification_preference import NotificationPreference
from backend.models.pinned_post import PinnedPost
from backend.models.playbook import PlaybookTemplate, PlaybookTemplateVersion
from backend.models.portal import (
    IdentityMode,
    ProfileVisibility,
    RepositorySource,
    UserConnectedAccount,
    UserPrivacySettings,
    UserRepository,
    UserSocialLink,
)
from backend.models.post import Post
from backend.models.post_release_note import PostReleaseNote
from backend.models.post_version import PostVersion
from backend.models.prompt_metadata import PromptMetadata
from backend.models.report import Report
from backend.models.revision import Revision
from backend.models.subscription import Subscription
from backend.models.tag import PostTag, Tag
from backend.models.thread_subscription import ThreadSubscription
from backend.models.user import User
from backend.models.user_post_read import UserPostRead
from backend.models.vote import Vote
from backend.models.workspace import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
    WorkspaceMemberRole,
    WorkspaceVisibility,
)

__all__ = [
    "BenchmarkCase",
    "BenchmarkRun",
    "BenchmarkRunResult",
    "BenchmarkRunStatus",
    "BenchmarkSuite",
    "AIProvider",
    "AIReviewRequest",
    "AIReviewResult",
    "AIReviewStatus",
    "AIReviewType",
    "AnalyticsEvent",
    "AuditLog",
    "Badge",
    "Bookmark",
    "Comment",
    "CommentAttachment",
    "ContentLink",
    "DigestRun",
    "EmailDeliveryLog",
    "Follow",
    "IdentityMode",
    "NewsletterSubscription",
    "Notification",
    "NotificationPreference",
    "PinnedPost",
    "Subscription",
    "PlaybookTemplate",
    "PlaybookTemplateVersion",
    "Post",
    "PostReleaseNote",
    "PostTag",
    "PostVersion",
    "PromptMetadata",
    "ProfileVisibility",
    "Report",
    "RepositorySource",
    "VALID_LINK_TYPES",
    "Revision",
    "SiteSetting",
    "Tag",
    "ThreadSubscription",
    "User",
    "UserBadge",
    "UserConnectedAccount",
    "UserPostRead",
    "UserPrivacySettings",
    "UserRepository",
    "UserSocialLink",
    "Vote",
    "Workspace",
    "WorkspaceInvitation",
    "WorkspaceMember",
    "WorkspaceMemberRole",
    "WorkspaceVisibility",
]
