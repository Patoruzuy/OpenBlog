"""Flask application factory.

Usage
-----
Gunicorn::

    gunicorn -c gunicorn.conf.py "backend.app:create_app()"

Flask dev server::

    flask --app "backend.app:create_app()" run

Programmatic (tests / Celery worker)::

    from backend.app import create_app
    app = create_app("testing")
"""

from __future__ import annotations

import os

from flask import Flask

from backend import extensions
from backend.config import DevelopmentConfig, config_map
from backend.utils.logging import configure_logging


def create_app(config_name: str | None = None) -> Flask:
    """Create and configure the Flask application.

    Parameters
    ----------
    config_name:
        One of ``"development"``, ``"staging"``, ``"production"``,
        ``"testing"``.  Defaults to the value of the ``ENV`` environment
        variable, falling back to ``"development"``.
    """
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ── Config ────────────────────────────────────────────────────────────────
    cfg_name = config_name or os.environ.get("ENV", "development")
    config_cls = config_map.get(cfg_name, DevelopmentConfig)
    app.config.from_object(config_cls)

    # Raises RuntimeError with a clear message if required env vars are missing.
    # Skipped automatically for TestingConfig.
    config_cls.validate()

    # ── Logging ───────────────────────────────────────────────────────────────
    configure_logging(app)

    # ── Extensions ────────────────────────────────────────────────────────────
    # Binds db, csrf, redis client, celery instance to this app.
    extensions.init_app(app)

    # ── Observability ─────────────────────────────────────────────────────────
    # Request-ID middleware + structured access logging (always on).
    from backend.utils.request_id import init_request_logging  # noqa: PLC0415

    init_request_logging(app)

    # Prometheus /metrics endpoint + DB/Celery hooks (disabled in tests).
    if app.config.get("METRICS_ENABLED", True):
        from backend.utils.metrics import init_metrics  # noqa: PLC0415

        init_metrics(app)

    # ── Models ────────────────────────────────────────────────────────────────
    # Import all models so SQLAlchemy metadata is populated before any
    # db.create_all() call (tests) and Alembic autogenerate sees every table.
    import backend.models  # noqa: F401  (side-effect import)
    import backend.tasks  # noqa: F401  (registers Celery tasks for worker autodiscovery)

    # ── Blueprints ────────────────────────────────────────────────────────────
    _register_blueprints(app)

    # ── Per-request auth state ──────────────────────────────────────────────
    # Clear the cached current_user before every request so the g-based cache
    # in get_current_user() never leaks across requests.  This is a no-op in
    # production (each request gets a fresh app context anyway) but is critical
    # in tests where a single app context can span multiple test-client calls.
    @app.before_request
    def _clear_current_user_cache() -> None:
        from flask import g as _g

        from backend.utils.auth import _UNSET

        _g._current_user = _UNSET  # type: ignore[attr-defined]

    # ── Template context ────────────────────────────────────────────────────
    # Inject ``current_user`` and ``current_locale`` into every template so
    # the nav bar and language switcher can display state without explicit
    # view arguments.
    @app.context_processor
    def _inject_current_user() -> dict:
        from datetime import UTC, datetime  # noqa: PLC0415

        from flask_babel import get_locale  # noqa: PLC0415

        from backend.utils.auth import (
            get_current_user,  # local to avoid circular import
        )

        user = get_current_user()
        unread = 0
        if user is not None:
            try:
                redis = app.extensions.get("redis")
                cache_key = f"notif_unread:{user.id}"
                cached = redis.get(cache_key) if redis is not None else None
                if cached is not None:
                    unread = int(cached)
                else:
                    from backend.services.notification_service import (
                        NotificationService,  # noqa: PLC0415
                    )

                    unread = NotificationService.unread_count(user.id)
                    if redis is not None:
                        redis.set(cache_key, unread, ex=30)
            except Exception as exc:
                app.logger.warning(
                    "Failed to fetch unread count for user %s: %s", user.id, exc
                )
                unread = 0
        return {
            "current_user": user,
            "unread_notifications": unread,
            "current_locale": str(get_locale() or "en"),
            "current_year": lambda: datetime.now(UTC).year,
        }

    # ── Jinja2 globals ───────────────────────────────────────────────────────
    _register_jinja_globals(app)

    # ── CLI commands ──────────────────────────────────────────────────────────
    _register_cli(app)

    app.logger.info("OpenBlog started (env=%s, debug=%s)", cfg_name, app.debug)
    return app


def _register_blueprints(app: Flask) -> None:
    """Import and register all blueprints inside the factory.

    Keeping imports here (rather than at module level) reduces cold-import
    cost and means blueprint-level import errors surface during
    ``create_app()`` rather than at module load time.
    """
    from backend.routes.admin import admin_bp
    from backend.routes.api.analytics import api_analytics_bp
    from backend.routes.api.auth import api_auth_bp
    from backend.routes.api.badges import api_badges_bp
    from backend.routes.api.bookmarks import api_bookmarks_bp
    from backend.routes.api.comments import api_comments_bp
    from backend.routes.api.notifications import api_notifications_bp
    from backend.routes.api.posts import api_posts_bp
    from backend.routes.api.reports import api_reports_bp
    from backend.routes.api.revisions import api_revisions_bp
    from backend.routes.api.search import api_search_bp
    from backend.routes.api.thread_follow import api_thread_follow_bp
    from backend.routes.api.users import api_users_bp
    from backend.routes.api.votes import api_votes_bp
    from backend.routes.attachments import api_attachments_bp, attachments_bp
    from backend.routes.auth import ssr_auth_bp
    from backend.routes.bookmarks import ssr_bookmarks_bp
    from backend.routes.drafts import ssr_drafts_bp
    from backend.routes.explore import explore_bp
    from backend.routes.feed import feed_bp
    from backend.routes.health import health_bp
    from backend.routes.i18n import i18n_bp
    from backend.routes.improvements import improvements_bp
    from backend.routes.index import index_bp
    from backend.routes.json_feed import json_feed_bp
    from backend.routes.newsletter import newsletter_bp
    from backend.routes.notifications import ssr_notifications_bp
    from backend.routes.pages import pages_bp
    from backend.routes.posts import ssr_posts_bp
    from backend.routes.revisions import ssr_revisions_bp
    from backend.routes.search import ssr_search_bp
    from backend.routes.settings import settings_bp
    from backend.routes.sitemap import sitemap_bp
    from backend.routes.tags import ssr_tags_bp
    from backend.routes.threads import threads_bp
    from backend.routes.users import ssr_users_bp
    from backend.routes.workspace import workspace_bp
    from backend.routes.invites import invite_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(i18n_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(index_bp)
    app.register_blueprint(explore_bp)
    app.register_blueprint(ssr_auth_bp)
    app.register_blueprint(api_auth_bp)
    app.register_blueprint(ssr_drafts_bp)
    app.register_blueprint(ssr_posts_bp)
    app.register_blueprint(api_posts_bp)
    app.register_blueprint(ssr_revisions_bp)
    app.register_blueprint(api_comments_bp)
    app.register_blueprint(ssr_search_bp)
    app.register_blueprint(api_search_bp)
    app.register_blueprint(ssr_users_bp)
    app.register_blueprint(api_users_bp)
    app.register_blueprint(ssr_tags_bp)
    app.register_blueprint(ssr_bookmarks_bp)
    app.register_blueprint(ssr_notifications_bp)
    app.register_blueprint(api_votes_bp)
    app.register_blueprint(api_bookmarks_bp)
    app.register_blueprint(api_notifications_bp)
    app.register_blueprint(api_revisions_bp)
    app.register_blueprint(api_badges_bp)
    app.register_blueprint(api_analytics_bp)
    app.register_blueprint(api_reports_bp)
    app.register_blueprint(api_thread_follow_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(api_attachments_bp)
    app.register_blueprint(newsletter_bp)
    app.register_blueprint(threads_bp)
    app.register_blueprint(improvements_bp)
    app.register_blueprint(feed_bp)
    app.register_blueprint(json_feed_bp)
    app.register_blueprint(sitemap_bp)
    app.register_blueprint(workspace_bp)
    app.register_blueprint(invite_bp)


def _register_jinja_globals(app: Flask) -> None:
    """Register utility functions available in every Jinja2 template."""
    import hashlib
    import os as _os
    from urllib.parse import urlencode

    from flask import url_for as _url_for

    # Compute an 8-char content hash of main.css once at startup so that any
    # CSS/JS change produces a new URL, busting the browser's immutable cache.
    _css_path = _os.path.join(app.static_folder, "css", "main.css")
    try:
        with open(_css_path, "rb") as _fh:
            _static_ver = hashlib.md5(_fh.read()).hexdigest()[:8]  # noqa: S324
    except OSError:
        _static_ver = "1"
    app.jinja_env.globals["static_ver"] = _static_ver

    def url_with_query(endpoint: str, _qs: dict | None = None, **url_kwargs) -> str:  # noqa: ANN001
        """Return ``url_for(endpoint, **url_kwargs)`` with *_qs* appended as
        query-string parameters.

        Use *_qs* for keys that are Python/Jinja2 reserved words (e.g. ``from``)::

            {{ url_with_query('posts.compare', slug=post.slug,
                              _qs={'from': 1, 'to': 3}) }}
        """
        url: str = _url_for(endpoint, **url_kwargs)
        if _qs:
            sep = "&" if "?" in url else "?"
            url += sep + urlencode(_qs)
        return url

    app.jinja_env.globals["url_with_query"] = url_with_query

    from backend.utils.seo import absolute_url, canonical_url

    app.jinja_env.globals["absolute_url"] = absolute_url
    app.jinja_env.globals["canonical_url"] = canonical_url


def _register_cli(app: Flask) -> None:
    """Register custom Flask CLI commands."""
    import click

    @app.cli.command("seed")
    def seed_command() -> None:  # type: ignore[return]
        """Seed the database with demo data (admin user + sample posts).

        Safe to run multiple times — existing records are skipped.
        """
        click.echo("Seeding database...")
        from backend.scripts.seed import run_seed

        run_seed()
        click.echo("Done.")
