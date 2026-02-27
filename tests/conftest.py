"""Pytest fixtures shared across the unit test suite.

The `app` fixture uses TestingConfig which:
  - hardcodes all required config values (no .env dependency)
  - sets TESTING=True (skips startup validation)
  - uses SQLite in-memory with StaticPool (all connections share the same DB)
  - disables CSRF protection (simplifies form testing)
  - disables Flask-Limiter (no Redis required)

The `db_session` fixture creates all tables before the test and drops them
afterward.  It also replaces the Redis client with a lightweight in-memory
stub so auth tests can run without a live Redis server.

The `auth_client` fixture depends on `db_session` and returns a Flask test
client backed by the live in-memory SQLite database.
"""

from __future__ import annotations

import fakeredis
import pytest
from sqlalchemy import text as sa_text

from backend.app import create_app
from backend.extensions import db as _db

# ── Session-scoped app ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def app():
    """Create a single Flask app for the entire unit test session."""
    return create_app("testing")


@pytest.fixture
def client(app):
    """Return a fresh Flask test client for each test function."""
    return app.test_client()


# ── DB-backed fixtures (function-scoped) ─────────────────────────────────────


@pytest.fixture
def db_session(app):
    """Provide a live SQLite session with all tables created.

    Replaces ``app.extensions["redis"]`` with ``fakeredis.FakeRedis`` for the duration of
    the test so that AuthService calls (setex/exists/delete) succeed without
    a running Redis server.

    Guarantees a clean schema for each test: creates all tables before yield,
    removes the session and drops all tables after.
    """
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    original_redis = app.extensions.get("redis")
    app.extensions["redis"] = fake_redis

    with app.app_context():
        _db.create_all()
        yield _db.session
        _db.session.remove()
        # Drop every table present in the live DB (not just what metadata knows).
        # Use IF EXISTS + FK-off so the PostVersion ↔ Revision circular FK
        # never blocks a drop, and no "index already exists" leaks to the
        # next test's create_all().
        with _db.engine.begin() as conn:
            conn.execute(sa_text("PRAGMA foreign_keys = OFF"))
            rows = conn.execute(
                sa_text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ).fetchall()
            for (table_name,) in rows:
                conn.execute(sa_text(f'DROP TABLE IF EXISTS "{table_name}"'))
            conn.execute(sa_text("PRAGMA foreign_keys = ON"))

    # Restore the real Redis client so other test families are unaffected.
    if original_redis is not None:
        app.extensions["redis"] = original_redis


@pytest.fixture
def auth_client(app, db_session):  # noqa: ARG001
    """Flask test client backed by a live in-memory SQLite database.

    Depends on ``db_session`` which ensures tables exist and Redis is mocked
    before any requests are made.
    """
    return app.test_client()


@pytest.fixture
def make_user_token(app, db_session):  # noqa: ARG002
    """Factory fixture: create a user with *role* and return (user, access_token).

    Usage::

        def test_something(auth_client, make_user_token):
            user, token = make_user_token("contrib@example.com", "contrib", role="contributor")
            resp = auth_client.get("/api/posts/", headers={"Authorization": f"Bearer {token}"})
    """
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    _counter = {"n": 0}

    def _make(
        email: str | None = None,
        username: str | None = None,
        *,
        role: str = "reader",
        password: str = "StrongPass123!!",
    ):
        _counter["n"] += 1
        n = _counter["n"]
        email = email or f"user{n}@example.com"
        username = username or f"user{n}"
        # db_session already pushed an app_context — no nested push needed.
        user = AuthService.register(email, username, password)
        if role != "reader":
            user.role = UserRole(role)
            _db.session.commit()
        token = AuthService.issue_access_token(user)
        return user, token

    return _make

