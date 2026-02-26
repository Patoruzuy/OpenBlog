"""Structured logging configuration.

- Production / Staging: JSON lines to stdout (machine-readable for log aggregators).
- Development / Testing: human-readable werkzeug-style format.

Attach to the Flask app logger via configure_logging(app), called from
create_app() before any blueprints are registered.

JSON log records are enriched with:
  - ``request_id``  — from flask.g when inside a request context
  - ``method``, ``path``, ``status``, ``duration_ms``, ``user_id``,
    ``remote_addr`` — injected via ``extra={...}`` by the request middleware
  - ``task_id``, ``task_name`` — injected by Celery task logging
  - ``exception``   — full traceback when exc_info is attached
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from flask import Flask

# Extra field names copied from LogRecord into the JSON payload.
_EXTRA_FIELDS = (
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
    "user_id",
    "remote_addr",
    "task_id",
    "task_name",
)


class _JsonFormatter(logging.Formatter):
    """Emit a single JSON object per log record.

    Enriched with request context (``request_id``, user info) when called
    inside a Flask request context, extra fields supplied via
    ``logger.info(..., extra={...})``, and exception details when
    ``exc_info`` is set on the record.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
        }

        # ── Flask request context ─────────────────────────────────────────
        try:
            from flask import g, has_request_context  # noqa: PLC0415

            if has_request_context():
                rid = getattr(g, "request_id", None)
                if rid and "request_id" not in payload:
                    payload["request_id"] = rid
        except Exception:  # noqa: BLE001
            pass

        # ── Extra fields injected via logger.xxx(..., extra={...}) ────────
        for key in _EXTRA_FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val

        # ── Exception / traceback ─────────────────────────────────────────
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text

        return json.dumps(payload)


def configure_logging(app: Flask) -> None:
    """Attach an appropriate handler to the Flask app logger.

    Called once per app factory invocation.  Clears any existing handlers to
    avoid duplicate log lines when create_app() is called multiple times in tests.
    """
    handler = logging.StreamHandler()
    env = app.config.get("ENV", "development")

    if env in ("production", "staging"):
        handler.setFormatter(_JsonFormatter())
        log_level = logging.INFO
    else:
        fmt = "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
        log_level = logging.DEBUG

    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(log_level)
    # Prevent propagation to the root logger (avoids duplicate output)
    app.logger.propagate = False


def configure_celery_logging(env: str = "development") -> None:
    """Configure the root logger used by Celery workers.

    Call this once from the Celery worker entrypoint (``celery_worker.py``)
    after the Flask app is created so that Celery task logs use the same
    format as the web process.

    In production/staging, a JSON formatter is attached; otherwise a
    human-readable format is used.
    """
    handler = logging.StreamHandler()
    if env in ("production", "staging"):
        handler.setFormatter(_JsonFormatter())
        log_level = logging.INFO
    else:
        fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
        log_level = logging.DEBUG

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)
