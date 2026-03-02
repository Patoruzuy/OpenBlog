# Testing Guide

## Test suite overview

| Suite | Count | Requires | Command |
|-------|-------|----------|---------|
| Unit (default) | ~1 778 | Nothing (SQLite in-memory, fakeredis) | `pytest` |
| Integration | 4 | Docker: PostgreSQL + Redis | `pytest --run-integration` |
| Both | ~1 782 | Docker | `RUN_INTEGRATION_TESTS=1 pytest` |

---

## Running the unit suite

```bash
# All unit tests (Docker NOT required)
pytest

# Verbose output
pytest -v

# Stop on first failure
pytest -x

# Run a single file
pytest tests/test_metrics.py

# Run a single test
pytest tests/test_metrics.py::TestMetricsEndpoint::test_metrics_endpoint_returns_200
```

The default run skips every test marked `@pytest.mark.integration` and
produces a `SKIPPED` entry (not a failure) for each one.

---

## Running the integration suite

Integration tests hit real PostgreSQL and Redis instances managed by Docker
Compose.

### Prerequisites

```bash
# Start all services
make up
# or
docker compose up -d

# Confirm all containers are healthy
docker compose ps
```

### Running

```bash
# CLI flag
pytest --run-integration

# Environment variable (useful in CI)
RUN_INTEGRATION_TESTS=1 pytest

# Integration tests only (fastest for debugging)
pytest --run-integration -m integration -v
```

If Docker services are not reachable when `--run-integration` is active, all
integration tests are skipped immediately with a clear message telling you
which service is down and how to fix it.

---

## Running both suites (full CI run)

```bash
make up
RUN_INTEGRATION_TESTS=1 pytest --tb=short
```

---

## CI configuration examples

### GitHub Actions

```yaml
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: pytest --tb=short -q       # no Docker needed

  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: postgres }
        ports: ["5432:5432"]
        options: --health-cmd pg_isready
      redis:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: RUN_INTEGRATION_TESTS=1 pytest -m integration --tb=short -v
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/test
          REDIS_URL: redis://localhost:6379/0
```

---

## Integration test gating

Integration tests are gated at two levels:

1. **Collection time** (`tests/conftest.py::pytest_collection_modifyitems`):
   Every item with `@pytest.mark.integration` is marked `SKIPPED` before
   setup runs unless `--run-integration` or `RUN_INTEGRATION_TESTS=1` is set.
   This means no Docker connection is attempted at all in the default run.

2. **Session setup** (`tests/integration/conftest.py::require_docker_services`):
   When integration tests are enabled, a session-scoped autouse fixture
   performs a fast TCP connectivity check against `localhost:5432`
   (PostgreSQL) and `localhost:6379` (Redis) **before** any test executes.
   If either service is unreachable, all integration tests are skipped
   immediately with a message pointing to `make up`.

---

## Metrics test stability

`TestMetricsEndpoint` in `tests/test_metrics.py` creates a second Flask app
with `METRICS_ENABLED=True` to test the `/metrics` scrape endpoint.
Previously this was order-sensitive: if the integration test suite ran first
(which it does alphabetically — `tests/integration/` < `tests/test_*`), the
`live_client` fixture called `create_app("development")`, which populated the
`_flask_metrics` global singleton, causing `init_metrics` to short-circuit and
never register the `/metrics` route on the test metrics app (→ 404).

**Fix**: `init_metrics` now uses a **per-app guard** stored in
`app.extensions["_openblog_prometheus_metrics"]` instead of a process-wide
singleton flag.  Subsequent apps reuse the already-registered global
Prometheus REGISTRY entries but each get their own `/metrics` URL rule, so
`app.test_client().get("/metrics")` always returns 200 regardless of test
execution order.

---

## Common troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Integration tests fail with `503` | Docker services not running | `make up` |
| `ValueError: Duplicated timeseries` | Multiple `PrometheusMetrics` instances | should not happen after the fix; if it does, restart the test session |
| `PermissionError` in pytest temp cleanup | Windows filesystem race | cosmetic; tests still pass |
| `DeprecationWarning: datetime.utcnow` | Third-party code | suppressed via `filterwarnings` in `pyproject.toml` |
