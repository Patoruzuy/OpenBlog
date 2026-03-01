"""Application configuration.

load_dotenv() is called here — before class bodies execute — so that
os.environ.get() picks up values from a .env file on all code paths
(Flask dev server, gunicorn, celery worker, pytest).
"""

from __future__ import annotations

import os
from typing import ClassVar

from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool

# Must run before any class-body os.environ.get() calls.
load_dotenv()


class BaseConfig:
    """Shared defaults.  All values are resolved from os.environ at import time."""

    ENV: str = os.environ.get("ENV", "development")

    # ── Security ───────────────────────────────────────────────────────────
    SECRET_KEY: str | None = os.environ.get("SECRET_KEY")
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    # Default True — only dev/testing configs override this to False.
    SESSION_COOKIE_SECURE: bool = True

    # ── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str | None = os.environ.get("DATABASE_URL")
    # Flask-SQLAlchemy reads SQLALCHEMY_DATABASE_URI
    SQLALCHEMY_DATABASE_URI: str | None = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict] = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }

    # ── Redis ──────────────────────────────────────────────────────────────
    REDIS_URL: str | None = os.environ.get("REDIS_URL")

    # ── Celery — derived from REDIS_URL; do not set independently ──────────
    CELERY_BROKER_URL: str | None = os.environ.get("REDIS_URL")
    CELERY_RESULT_BACKEND: str | None = os.environ.get("REDIS_URL")

    # ── JWT ────────────────────────────────────────────────────────────────
    # Falls back to SECRET_KEY so a single env var covers both uses.
    JWT_SECRET_KEY: str | None = os.environ.get("JWT_SECRET_KEY") or os.environ.get(
        "SECRET_KEY"
    )
    # Token lifetimes in seconds.
    ACCESS_TOKEN_EXPIRY: int = int(
        os.environ.get("ACCESS_TOKEN_EXPIRY", "900")
    )  # 15 min
    REFRESH_TOKEN_EXPIRY: int = int(
        os.environ.get("REFRESH_TOKEN_EXPIRY", "604800")
    )  # 7 days

    # ── Rate limiting (Flask-Limiter) ──────────────────────────────────────
    RATELIMIT_ENABLED: bool = True
    RATELIMIT_STORAGE_URI: str | None = os.environ.get("REDIS_URL")
    RATELIMIT_HEADERS_ENABLED: bool = True  # X-RateLimit-* response headers

    # ── CSRF ───────────────────────────────────────────────────────────────
    WTF_CSRF_ENABLED: bool = True

    # ── Email (Flask-Mail) ────────────────────────────────────────────────
    MAIL_SERVER: str = os.environ.get("MAIL_SERVER", "localhost")
    MAIL_PORT: int = int(os.environ.get("MAIL_PORT", "1025"))
    MAIL_USE_TLS: bool = os.environ.get("MAIL_USE_TLS", "false").lower() == "true"
    MAIL_USE_SSL: bool = os.environ.get("MAIL_USE_SSL", "false").lower() == "true"
    MAIL_USERNAME: str | None = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD: str | None = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER: str = os.environ.get(
        "MAIL_DEFAULT_SENDER", "noreply@openblog.dev"
    )
    MAIL_SUPPRESS_SEND: bool = False

    # ── Media / file storage ─────────────────────────────────────────
    # Absolute path to the directory used for user-uploaded files.
    # Must be writable by the app process and must NOT be under static/.
    MEDIA_ROOT: str = os.environ.get("MEDIA_ROOT", "/app/media")
    # 5 MiB hard limit for a single comment attachment.
    MAX_COMMENT_ATTACHMENT_BYTES: int = int(
        os.environ.get("MAX_COMMENT_ATTACHMENT_BYTES", str(5 * 1024 * 1024))
    )

    # ── Newsletter ──────────────────────────────────────────────────
    NEWSLETTER_CONFIRM_TTL: int = 48 * 60 * 60  # 48 h in seconds
    SITE_NAME: str = os.environ.get("SITE_NAME", "OpenBlog")
    SITE_URL: str = os.environ.get("SITE_URL", "https://openblog.dev")
    # Absolute base URL for generated links (RSS, sitemap, canonical, OG).
    # Required in production — e.g. https://openblog.dev (no trailing slash).
    PUBLIC_BASE_URL: str | None = os.environ.get("PUBLIC_BASE_URL")

    # ── Thread notifications ────────────────────────────────────────
    # Minimum seconds between two "new comment on thread you follow" emails
    # for the same (user, post) pair.  Prevents inbox flooding on busy threads.
    # Set to 0 to disable the cooldown (every comment triggers an email).
    THREAD_NOTIF_COOLDOWN_SECONDS: int = int(
        os.environ.get("THREAD_NOTIF_COOLDOWN_SECONDS", "900")  # 15 min default
    )

    # ── Flags ──────────────────────────────────────────────────────────────
    DEBUG: bool = False
    TESTING: bool = False

    # ── Observability ──────────────────────────────────────────────────────
    # Set False in TestingConfig to avoid registering the /metrics endpoint
    # and SQLAlchemy event hooks on ephemeral in-memory test databases.
    METRICS_ENABLED: bool = True

    # ── Celery beat schedule ───────────────────────────────────────────────
    CELERYBEAT_SCHEDULE: ClassVar[dict] = {
        "publish-scheduled-posts": {
            "task": "tasks.publish_scheduled_posts",
            "schedule": 60.0,  # every 60 seconds
        },
        "flush-analytics-queue": {
            "task": "tasks.flush_analytics_queue",
            "schedule": 30.0,  # every 30 seconds
        },
    }

    # ── Internationalisation (Flask-Babel) ────────────────────────────────
    BABEL_DEFAULT_LOCALE: str = "en"
    BABEL_DEFAULT_TIMEZONE: str = "UTC"
    SUPPORTED_LOCALES: ClassVar[list[str]] = ["en", "es"]
    # "translations" is Flask-Babel's default directory (relative to app.root_path)

    # Required config keys validated on startup (skipped for TestingConfig)
    _REQUIRED: ClassVar[list[str]] = [
        "SECRET_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "PUBLIC_BASE_URL",
    ]

    @classmethod
    def validate(cls) -> None:
        """Raise RuntimeError if any required config value is None or empty.

        Validation is skipped when TESTING=True so the unit test suite does
        not require real credentials.
        """
        if cls.TESTING:
            return

        missing = [name for name in cls._REQUIRED if not getattr(cls, name, None)]
        if missing:
            raise RuntimeError(
                "OpenBlog startup error: missing required config: "
                f"{', '.join(missing)}. "
                "Check your .env file or environment variables."
            )


class DevelopmentConfig(BaseConfig):
    DEBUG: bool = True
    SESSION_COOKIE_SECURE: bool = False  # allow http in local dev


class StagingConfig(BaseConfig):
    DEBUG: bool = False
    SESSION_COOKIE_SECURE: bool = True


class ProductionConfig(BaseConfig):
    DEBUG: bool = False
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"


class TestingConfig(BaseConfig):
    """Hardcoded values — never reads from .env; validation is skipped."""

    TESTING: bool = True
    DEBUG: bool = True
    SESSION_COOKIE_SECURE: bool = False  # allow http in test runner
    WTF_CSRF_ENABLED: bool = False
    METRICS_ENABLED: bool = False

    SECRET_KEY: str = "test-secret-key-not-for-production"  # type: ignore[assignment]
    JWT_SECRET_KEY: str = "test-jwt-secret-not-for-production"  # type: ignore[assignment]
    DATABASE_URL: str = "sqlite:///:memory:"  # type: ignore[assignment]
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"  # type: ignore[assignment]
    # StaticPool: all connections share the same in-memory SQLite DB.
    # Required so that db.create_all() in fixtures and test-client requests
    # all see the same tables.
    SQLALCHEMY_ENGINE_OPTIONS: dict = {  # type: ignore[assignment]
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    REDIS_URL: str = "redis://localhost:6379/0"  # type: ignore[assignment]
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"  # type: ignore[assignment]
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"  # type: ignore[assignment]
    # Disable rate limiting in tests — no Redis required.
    RATELIMIT_ENABLED: bool = False  # type: ignore[assignment]
    RATELIMIT_STORAGE_URI: str = "memory://"  # type: ignore[assignment]
    MAIL_SUPPRESS_SEND: bool = True  # type: ignore[assignment]
    PUBLIC_BASE_URL: str = "http://testserver"  # type: ignore[assignment]


config_map: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "staging": StagingConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
