"""Conftest for Docker integration tests.

When ``--run-integration`` / ``RUN_INTEGRATION_TESTS=1`` is active, this
module performs a fast connectivity check against the Docker services before
any integration test is allowed to run.  If a required service is unreachable
the entire integration module is aborted with a clear, actionable message
instead of timing out inside individual tests.

Required services and default ports
------------------------------------
- PostgreSQL : localhost:5432
- Redis      : localhost:6379

Run ``make up`` (or ``docker compose up -d``) before enabling integration
tests.  See TESTING.md for full instructions.
"""

from __future__ import annotations

import socket

import pytest


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if a TCP connection to *host*:*port* succeeds within *timeout*s."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True, scope="session")
def require_docker_services() -> None:  # type: ignore[return]
    """Abort integration tests early when Docker services are not reachable.

    This fixture is session-scoped and autouse so it runs once before the
    first integration test.  If any required service is unreachable the whole
    session is skipped with a clear message rather than timing out per-test.
    """
    checks = {
        "PostgreSQL:5432": _tcp_reachable("localhost", 5432),
        "Redis:6379": _tcp_reachable("localhost", 6379),
    }
    missing = [name for name, ok in checks.items() if not ok]
    if missing:
        pytest.skip(
            f"Docker service(s) not reachable: {', '.join(missing)}.  "
            "Run 'make up' (or 'docker compose up -d') and re-run with "
            "pytest --run-integration"
        )
    yield
