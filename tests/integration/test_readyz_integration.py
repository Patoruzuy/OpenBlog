"""Integration tests for the /readyz endpoint.

These tests require real PostgreSQL and Redis services to be running.

Run with::

    pytest -m integration -v

Or via Makefile::

    make test-integration

Prerequisites:
    - ``make up`` (all Docker services healthy)
    - The ``ENV`` environment variable must be set to ``development`` in .env,
      or services must be reachable at the URLs in the config.

These tests are intentionally excluded from the default ``make test`` run so
the unit suite remains fast and dependency-free.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_client():
    """Create a test client using the real development config.

    Reads DATABASE_URL and REDIS_URL from the environment / .env file.
    Requires running PostgreSQL and Redis services.
    """
    from backend.app import create_app

    app = create_app("development")
    return app.test_client()


def test_readyz_live_returns_200(live_client):
    response = live_client.get("/readyz")
    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. "
        "Ensure 'make up' has been run and all services are healthy."
    )


def test_readyz_live_db_ok(live_client):
    response = live_client.get("/readyz")
    data = response.get_json()
    assert data["db"] == "ok", f"DB check failed: {data}"


def test_readyz_live_redis_ok(live_client):
    response = live_client.get("/readyz")
    data = response.get_json()
    assert data["redis"] == "ok", f"Redis check failed: {data}"


def test_readyz_live_overall_status_ok(live_client):
    response = live_client.get("/readyz")
    data = response.get_json()
    assert data["status"] == "ok", f"Overall status degraded: {data}"
