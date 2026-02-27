"""System health service — lightweight operational visibility."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from sqlalchemy import text


class SystemHealthService:
    @staticmethod
    def get_status() -> dict:
        """Return a dict of health indicators for the system view."""
        return {
            "db":     _check_db(),
            "redis":  _check_redis(),
            "celery": _check_celery(),
            "python": sys.version,
            "server_time": datetime.now(UTC).isoformat(),
        }


def _check_db() -> dict:
    try:
        from backend.extensions import db  # noqa: PLC0415
        db.session.execute(text("SELECT 1"))
        return {"ok": True, "label": "Connected"}
    except Exception as exc:
        return {"ok": False, "label": str(exc)[:120]}


def _check_redis() -> dict:
    try:
        from flask import current_app  # noqa: PLC0415
        redis = current_app.extensions.get("redis")
        if redis is None:
            return {"ok": False, "label": "Not configured"}
        redis.ping()
        info = redis.info("server")
        version = info.get("redis_version", "?")
        return {"ok": True, "label": f"Connected (v{version})"}
    except Exception as exc:
        return {"ok": False, "label": str(exc)[:120]}


def _check_celery() -> dict:
    try:
        from backend.extensions import celery  # noqa: PLC0415
        inspect = celery.control.inspect(timeout=1.5)
        stats = inspect.stats()
        if stats:
            worker_names = list(stats.keys())
            return {"ok": True, "label": f"{len(worker_names)} worker(s)", "workers": worker_names}
        return {"ok": False, "label": "No workers responding"}
    except Exception as exc:
        return {"ok": False, "label": str(exc)[:120]}
