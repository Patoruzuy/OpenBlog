"""Celery worker / beat entrypoint.

Usage
-----
Worker::

    celery -A backend.celery_worker.celery worker --loglevel=info

Beat scheduler::

    celery -A backend.celery_worker.celery beat \\
        --loglevel=info \\
        --scheduler=celery.beat.PersistentScheduler \\
        --schedule=/tmp/celerybeat-schedule

The ``celery`` name at module level is the configured Celery instance that the
CLI resolves via the ``-A backend.celery_worker.celery`` argument.

Design notes
------------
- We call ``create_app()`` to get a fully initialised Flask app (extensions
  bound, config validated).
- The Celery instance is retrieved from ``app.extensions["celery"]`` — it was
  created by ``extensions._make_celery(app)`` with the proper ``FlaskTask``
  base class that wraps each task in ``with app.app_context()``.
- No global ``app.app_context().push()`` — context lifetime is per-task.
"""

from __future__ import annotations

from backend.app import create_app

_flask_app = create_app()

# Configure the root logger for the worker process to use the same structured
# format as the web process (JSON in production, human-readable in development).
from backend.utils.logging import configure_celery_logging  # noqa: E402

configure_celery_logging(_flask_app.config.get("ENV", "development"))

# Exposed for the Celery CLI entrypoint
celery = _flask_app.extensions["celery"]
