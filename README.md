# OpenBlog

A production-ready developer blog platform with GitHub-style collaborative editing.
**Phase 1:** Scaffold & Infrastructure.

---

## Quick Start (Docker)

```bash
cp .env.example .env
make up

curl http://localhost/livez    # → {"status":"ok"}
curl http://localhost/readyz   # → {"status":"ok","db":"ok","redis":"ok"}
```

## Local Development (Poetry)

**Prerequisites:** Python 3.12+, [Poetry](https://python-poetry.org/)

```bash
# 1. Install dependencies and generate poetry.lock (commit this file)
poetry install

# 2. Copy the base env and the localhost overrides
cp .env.example .env
cp .env.local.example .env.local      # localhost DB/Redis URLs for flask run

# 3. Run the dev server
flask --app "backend.app:create_app()" run
```

> **`.env` vs `.env.local`**  
> `.env` uses Docker service hostnames (`db`, `redis`) — correct for `make up`.  
> `.env.local` overrides `DATABASE_URL` and `REDIS_URL` with `localhost` equivalents
> for running `flask run` directly on your machine. `python-dotenv` loads both;
> `.env.local` values take precedence. **Never commit `.env.local`** — it is in `.gitignore`.

## Running Tests

```bash
make test                # Unit suite — no Docker required (mocked DB + Redis)
make test-integration    # Integration suite — requires `make up` first
```

## Code Quality

```bash
make lint      # ruff check (zero tolerance)
make format    # ruff format
```

## Make Targets

| Target              | Description                                    |
|---------------------|------------------------------------------------|
| `make up`           | Build images and start all services            |
| `make down`         | Stop and remove containers                     |
| `make build`        | Rebuild images without starting                |
| `make logs`         | Follow all service logs                        |
| `make test`         | Run unit tests (no Docker required)            |
| `make test-integration` | Run integration tests (requires services)  |
| `make lint`         | Lint with ruff                                 |
| `make format`       | Format with ruff                               |
| `make shell`        | Open Flask shell in web container              |
| `make migrate`      | ⚠️ Not available until Phase 2                 |

## Services

| Service  | Port           | Description                            |
|----------|----------------|----------------------------------------|
| `nginx`  | `80` (public)  | Reverse proxy + static file serving    |
| `web`    | `8000` (internal) | Flask / Gunicorn application        |
| `db`     | `5432` (internal) | PostgreSQL 16                       |
| `redis`  | `6379` (internal) | Redis 7 (Celery broker + cache)     |
| `worker` | —              | Celery worker                          |
| `beat`   | —              | Celery beat scheduler                  |

## Phase 1 — Known Limitations

The following will be addressed in **Phase 2**:

- No database models exist yet — `/readyz` DB check requires Alembic migrations to run (`make migrate`).
- Celery worker and beat start with no registered tasks — benign startup warnings expected.
- CSP header is `Report-Only` with a permissive policy — tighten before Phase 7 (CodeMirror).
- HSTS is disabled — enable only after TLS is configured at Nginx (production).
- `flask run` uses `localhost` overrides for local development; Docker hostnames (`db`, `redis`) will not resolve on host.

## Project Structure

```
OpenBlog/
├── backend/
│   ├── app.py               # Application factory (create_app)
│   ├── config.py            # Config classes: Dev/Staging/Prod/Testing
│   ├── extensions.py        # SQLAlchemy, CSRF, Celery, Redis init
│   ├── celery_worker.py     # Celery entrypoint (worker + beat)
│   ├── routes/
│   │   ├── health.py        # GET /livez, GET /readyz
│   │   └── index.py         # GET /
│   ├── utils/
│   │   └── logging.py       # JSON logging (prod) / human-readable (dev)
│   ├── templates/
│   │   └── base.html        # Base Jinja2 template
│   └── static/css/
│       └── main.css         # Dark-mode design tokens + base styles
├── tests/
│   ├── conftest.py          # app + client fixtures (TestingConfig)
│   ├── test_health.py       # /livez + /readyz unit tests (mocked)
│   ├── test_index.py        # / route tests
│   └── integration/
│       └── test_readyz_integration.py  # Real DB + Redis (opt-in)
├── docker/
│   ├── Dockerfile           # Multi-stage: builder + non-root runtime
│   └── nginx/nginx.conf     # Reverse proxy + static serving
├── docker-compose.yml       # All 6 services with healthchecks
├── gunicorn.conf.py         # Production Gunicorn settings
├── pyproject.toml           # Poetry deps + ruff + pytest config
├── Makefile                 # Developer workflow targets
└── .env.example             # Required environment variables
```
