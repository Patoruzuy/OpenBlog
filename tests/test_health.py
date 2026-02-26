"""Unit tests for health check endpoints.

check_db() and check_redis() are monkeypatched in all /readyz tests so the
unit suite never requires a running PostgreSQL or Redis instance.
"""

from __future__ import annotations

import backend.routes.health as health_module  # noqa: E402 (imported for monkeypatching)

# ── /livez ────────────────────────────────────────────────────────────────────


def test_livez_returns_200(client):
    response = client.get("/livez")
    assert response.status_code == 200


def test_livez_returns_ok_status(client):
    response = client.get("/livez")
    data = response.get_json()
    assert data["status"] == "ok"


# ── /readyz ───────────────────────────────────────────────────────────────────


def test_readyz_returns_200_when_healthy(client, monkeypatch):
    monkeypatch.setattr(health_module, "check_db", lambda: True)
    monkeypatch.setattr(health_module, "check_redis", lambda: True)

    response = client.get("/readyz")
    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert data["redis"] == "ok"


def test_readyz_returns_503_when_db_down(client, monkeypatch):
    def _db_fail():
        raise ConnectionError("db unreachable")

    monkeypatch.setattr(health_module, "check_db", _db_fail)
    monkeypatch.setattr(health_module, "check_redis", lambda: True)

    response = client.get("/readyz")
    assert response.status_code == 503

    data = response.get_json()
    assert data["status"] == "degraded"
    assert data["db"] == "error"
    assert data["redis"] == "ok"


def test_readyz_returns_503_when_redis_down(client, monkeypatch):
    def _redis_fail():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(health_module, "check_db", lambda: True)
    monkeypatch.setattr(health_module, "check_redis", _redis_fail)

    response = client.get("/readyz")
    assert response.status_code == 503

    data = response.get_json()
    assert data["status"] == "degraded"
    assert data["db"] == "ok"
    assert data["redis"] == "error"


def test_readyz_response_has_required_keys(client, monkeypatch):
    monkeypatch.setattr(health_module, "check_db", lambda: True)
    monkeypatch.setattr(health_module, "check_redis", lambda: True)

    response = client.get("/readyz")
    data = response.get_json()

    assert "status" in data
    assert "db" in data
    assert "redis" in data


def test_readyz_returns_503_when_both_down(client, monkeypatch):
    def _fail():
        raise ConnectionError("service down")

    monkeypatch.setattr(health_module, "check_db", _fail)
    monkeypatch.setattr(health_module, "check_redis", _fail)

    response = client.get("/readyz")
    assert response.status_code == 503

    data = response.get_json()
    assert data["status"] == "degraded"
    assert data["db"] == "error"
    assert data["redis"] == "error"
