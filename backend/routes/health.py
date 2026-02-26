"""Health check endpoints.

/livez  — liveness probe  (always 200; confirms the process is alive)
/readyz — readiness probe  (200 when DB + Redis are reachable; 503 otherwise)

check_db() and check_redis() are module-level functions so that pytest
monkeypatch can replace them without touching SQLAlchemy or Redis internals:

    monkeypatch.setattr("backend.routes.health.check_db", lambda: True)
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from backend.extensions import db

health_bp = Blueprint("health", __name__)


# ── Connectivity check helpers (monkeypatchable) ──────────────────────────────


def check_db() -> bool:
    """Run a trivial SELECT 1 to confirm the DB connection is alive.

    Uses a fresh engine-level connection (not the session pool) so the probe
    accurately reflects raw connectivity rather than cached pool state.

    On PostgreSQL, a 3-second statement_timeout is set for this connection
    only (SET LOCAL) so the readyz probe cannot hang if the database is
    slow or overloaded.  The timeout is silently skipped on SQLite
    (used in unit tests).

    Raises an exception on failure (caught by the readyz handler).
    """
    with db.engine.connect() as conn:
        if db.engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL statement_timeout = '3s'"))
        conn.execute(text("SELECT 1"))
    return True


def check_redis() -> bool:
    """Ping the Redis client to confirm connectivity.

    Raises an exception on failure (caught by the readyz handler).
    """
    redis_client = current_app.extensions["redis"]
    redis_client.ping()
    return True


# ── Endpoints ─────────────────────────────────────────────────────────────────


@health_bp.get("/livez")
def livez():
    """Liveness probe — always 200.

    Returns ``{"status": "ok"}`` as long as the process is running.
    No dependency checks; used by container orchestrators to detect crashes.
    """
    return jsonify({"status": "ok"}), 200


@health_bp.get("/readyz")
def readyz():
    """Readiness probe — 200 when all dependencies are healthy, 503 otherwise.

    Checks PostgreSQL (via SQLAlchemy) and Redis (via ping).
    Used by load balancers and orchestrators to gate traffic.
    """
    status: dict[str, str] = {"db": "error", "redis": "error"}
    healthy = True

    try:
        check_db()
        status["db"] = "ok"
    except Exception:
        healthy = False

    try:
        check_redis()
        status["redis"] = "ok"
    except Exception:
        healthy = False

    http_code = 200 if healthy else 503
    return jsonify({"status": "ok" if healthy else "degraded", **status}), http_code
