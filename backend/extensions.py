"""Flask extension singletons and initialisation helpers.

Extension instances are created at module level (Flask pattern) so they can
be imported by route modules before the app is created.  Actual initialisation
(binding to an app) happens inside init_app(), called from create_app().
"""

from __future__ import annotations

from celery import Celery, Task
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from redis import Redis

# ── Extension singletons ──────────────────────────────────────────────────────
db: SQLAlchemy = SQLAlchemy()
csrf: CSRFProtect = CSRFProtect()
limiter: Limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    # Swallow storage errors so a Redis blip never breaks normal requests.
    swallow_errors=True,
)


def _make_celery(app: Flask) -> Celery:
    """Create a Celery instance whose tasks each run inside a Flask app context.

    Each task invocation is wrapped in ``with app.app_context(): ...`` via a
    custom Task base class.  This avoids the global app-context push anti-pattern
    and gives every task a clean, isolated context.

    The instance is stored at app.extensions["celery"] so that celery_worker.py
    can retrieve it as the CLI entrypoint without coupling to a module-level global.
    """

    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_instance = Celery(app.import_name, task_cls=FlaskTask)
    celery_instance.config_from_object(
        {
            "broker_url": app.config["CELERY_BROKER_URL"],
            "result_backend": app.config["CELERY_RESULT_BACKEND"],
        }
    )
    # Make this instance the Celery "current app" so @celery.task shortcuts
    # resolve correctly in future phases when tasks are registered.
    celery_instance.set_default()
    return celery_instance


def _init_redis(app: Flask) -> None:
    """Create a Redis client from REDIS_URL and store it in app.extensions."""
    client: Redis = Redis.from_url(
        app.config["REDIS_URL"],
        decode_responses=True,
    )
    app.extensions["redis"] = client


def init_app(app: Flask) -> None:
    """Bind all extensions to the Flask app instance."""
    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    _init_redis(app)
    celery_instance = _make_celery(app)
    app.extensions["celery"] = celery_instance
