# OpenBlog

> **A developer blog platform** — collaborative editing, AI-powered review, full-text search, digest emails, and deep observability. Built with Flask, Celery, and PostgreSQL. Ships as a single `docker compose up`.

![Python](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-1800%2B%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Code style](https://img.shields.io/badge/code%20style-ruff-orange)

---

### What makes it production-ready?

| | |
|---|---|
| ✍️ **Collaborative editing** | Contributor revisions, editor review, immutable version snapshots |
| 🤖 **AI Review Engine** | Async, workspace-scoped analysis with structured severity findings |
| 🔍 **Full-text search** | PostgreSQL `tsvector`/GIN, tunable ranking, tag feeds |
| 📬 **Digest emails** | Daily/weekly digests with idempotent retry and ops visibility |
| 📊 **Observability** | Prometheus metrics, structured JSON logs, `/livez` + `/readyz` |
| 🔒 **Security-first** | Argon2, CSRF, rate limiting, workspace isolation, scope-checked fanout |
| 🛠️ **Admin Ops Dashboard** | Health snapshot, AI review queue, digest history — all behind `Cache-Control: private, no-store` |

```bash
cp .env.example .env && make up
curl http://localhost/readyz   # → {"status":"ok","db":"ok","redis":"ok"}
```

---

## Quick Start (Docker)

```bash
cp .env.example .env        # fill in SECRET_KEY, DATABASE_URL, REDIS_URL, PUBLIC_BASE_URL
make up

curl http://localhost/livez    # → {"status":"ok"}
curl http://localhost/readyz   # → {"status":"ok","db":"ok","redis":"ok"}
```

## Local Development (Poetry)

**Prerequisites:** Python 3.12+, [Poetry](https://python-poetry.org/)

```bash
# 1. Install dependencies
poetry install

# 2. Copy env files
cp .env.example .env
cp .env.local.example .env.local      # localhost DB/Redis URLs for flask run

# 3. Run the dev server
flask --app "backend.app:create_app()" run
```

> **`.env` vs `.env.local`**  
> `.env` uses Docker service hostnames (`db`, `redis`) — correct for `make up`.  
> `.env.local` overrides `DATABASE_URL` and `REDIS_URL` with `localhost` equivalents
> for running `flask run` directly. `python-dotenv` loads both; `.env.local` wins.
> **Never commit `.env.local`** — it is git-ignored.

## Running Tests

```bash
make test                # Unit suite — no Docker required (SQLite in-memory)
make test-integration    # Integration suite — requires `make up` first
```

## Code Quality

```bash
make lint      # ruff check (zero tolerance)
make format    # ruff format
```

## Make Targets

| Target                  | Description                                      |
|-------------------------|--------------------------------------------------|
| `make up`               | Build images and start all services              |
| `make down`             | Stop and remove containers                       |
| `make build`            | Rebuild images without starting                  |
| `make logs`             | Follow all service logs                          |
| `make test`             | Run unit tests (no Docker required)              |
| `make test-integration` | Run integration tests (requires services)        |
| `make lint`             | Lint with ruff (zero tolerance)                  |
| `make format`           | Format with ruff                                 |
| `make shell`            | Open Flask shell in web container                |
| `make migrate`          | Run Alembic migrations                           |

## Services

| Service  | Port              | Description                             |
|----------|-------------------|-----------------------------------------|
| `nginx`  | `80` (public)     | Reverse proxy + static file serving     |
| `web`    | `8000` (internal) | Flask / Gunicorn application            |
| `db`     | `5432` (internal) | PostgreSQL 16                           |
| `redis`  | `6379` (internal) | Redis 7 (Celery broker + rate limiter)  |
| `worker` | —                 | Celery async worker (AI reviews, digests, notifications, email) |
| `beat`   | —                 | Celery beat (scheduled digests, publishing, maintenance)        |

## Features

### Content & Editing
- **Posts** — draft / published / scheduled / archived status lifecycle
- **Collaborative revisions** — contributor edit proposals reviewed by editors/admins
- **Post versioning** — immutable `PostVersion` snapshots on every accepted revision
- **Per-post changelog** — auto-generated release notes on post detail pages
- **Autosave** — periodic autosave with optimistic concurrency tokens
- **Rich markdown** — server-side rendering with Redis caching and reading-time estimates

### Users & Reputation
- **Roles** — reader, editor, admin
- **JWT auth** — short-lived access tokens + refresh rotation with Redis revocation
- **Reputation scores** — incremented on accepted revisions
- **Badges** — awarded automatically (first revision, prolific author, etc.)
- **Portals** — per-user identity modes (real name vs. handle) and privacy settings

### Discovery & Search
- **Full-text search** — PostgreSQL `tsvector`/GIN in production, SQLite LIKE in dev
- **Ranked results** — tunable weighted scoring (freshness, quality, personalisation)
- **Tags** — tag pages with RSS and JSON feeds
- **Explore** — recently improved, featured, and trending posts

### Social
- **Comments** with threaded subscriptions and notifications
- **Votes** (upvote/downvote on posts and comments)
- **Bookmarks** with SSR bookmark page
- **Follow** (user → user)
- **Notifications** — in-app + email digests (daily/weekly), tag follows, threaded grouping

### AI & Collaboration

- **AI Review Engine** — async, workspace-scoped AI analysis (clarity, architecture, security, full)
- **Suggestion → Revision workflow** — create human-reviewed revisions directly from AI suggestions
- **Structured findings** — severity-tagged insights with optional structured edit proposals
- **Workspace isolation** — AI review and suggestions never leak outside workspace scope

### Distribution & SEO
- **RSS 2.0** feeds (global + per-tag)
- **JSON Feed 1.1** (global + per-tag)
- **Sitemap** (`/sitemap.xml`) and `robots.txt`
- **HTTP caching** — ETag/304 on all feed endpoints
- **OG / Twitter Card meta tags** on post detail pages
- **Canonical URLs** and configurable SEO title/description per post

### Operations
- **Prometheus metrics** — request counters, DB query histograms, Celery task metrics
- **Structured JSON logging** in production; human-readable in dev
- **Request-ID middleware** — unique correlation IDs on every request
- **Health checks** — `/livez` (liveness) and `/readyz` (DB + Redis readiness)
- **Newsletter** — double opt-in with HMAC token verification
- **i18n** — Flask-Babel with `en` / `es` locale support
- **Admin dashboard** — user management, post moderation, report queue, analytics

### Admin Ops Dashboard

OpenBlog includes an admin-only operational dashboard for async systems:

- `/admin/ops` — health snapshot (DB, Redis, Celery) + 24h metrics
- `/admin/ops/ai-reviews` — filterable AI review requests with retry/cancel
- `/admin/ops/digests` — digest run history with retry support
- `/admin/ops/notifications` — aggregate notification stats and event distribution

#### Retry / Cancel Semantics

- **AI retry** — allowed only for `failed` or `canceled` requests. Resets status to `queued`, clears timestamps/errors, and re-enqueues the task.
- **AI cancel** — allowed only for `queued` or `running`. Marks `canceled`. If cancellation happens mid-task, the worker discards the result.
- **Digest retry** — allowed only for `failed`. The idempotency key is reset and the digest task is re-enqueued.

All admin routes are role-protected and return:

```
Cache-Control: private, no-store
```

No workspace content bodies are rendered in Ops views.

### Security
- **Argon2** password hashing
- **Flask-WTF CSRF** on all state-mutating SSR endpoints
- **Flask-Limiter** rate limiting on auth and submission endpoints (Redis-backed)
- **File uploads** — extension allowlist, UUID rename, stored outside `static/`
- **Secure session cookies** — `HttpOnly`, `SameSite=Lax`, `Secure=True` in production

## Project Structure

```
backend/
├── app.py                   # Application factory (create_app)
├── config.py                # Config classes: Dev / Staging / Prod / Testing
├── extensions.py            # SQLAlchemy, CSRF, Celery, Limiter, Redis init
├── models/                  # SQLAlchemy ORM models (20+ tables)
├── routes/
│   ├── api/                 # JSON REST endpoints (auth, posts, comments, ...)
│   └── *.py                 # SSR Jinja2 routes (posts, revisions, search, ...)
├── services/                # Business logic layer (39 service modules)
├── tasks/                   # Celery tasks (email, analytics, notifications, publish)
├── templates/               # Jinja2 HTML templates
├── utils/                   # Auth decorators, markdown renderer, SEO helpers, ...
└── migrations/              # Alembic migration scripts
tests/
├── conftest.py              # Shared fixtures (TestingConfig, fakeredis, db_session)
├── test_*.py                # 1 800+ unit tests (fast, deterministic)
└── integration/             # Real-DB integration tests (opt-in)
docker/
├── Dockerfile               # Multi-stage: builder + non-root runtime
└── nginx/nginx.conf         # Reverse proxy + static file serving
```

## Security & Isolation Invariants

OpenBlog enforces strict scope boundaries:

- Public feeds (`/feed.xml`, `/feed.json`, `/sitemap.xml`) include **only** published posts with `workspace_id IS NULL`.
- Workspace routes return `404` for non-members (fail-closed).
- Admin and workspace endpoints use `Cache-Control: private, no-store`.
- Async fanout (notifications, digests, AI reviews) re-checks visibility at delivery time.
- Dedupe fingerprints prevent duplicate notification or task execution on retry.

## Environment Variables

| Variable              | Required | Description                                       |
|-----------------------|----------|---------------------------------------------------|
| `SECRET_KEY`          | ✅       | Flask session signing key                         |
| `DATABASE_URL`        | ✅       | PostgreSQL connection string                      |
| `REDIS_URL`           | ✅       | Redis connection string (broker + cache)          |
| `PUBLIC_BASE_URL`     | ✅       | Canonical base URL, e.g. `https://openblog.dev`   |
| `JWT_SECRET_KEY`      |          | JWT signing key (falls back to `SECRET_KEY`)      |
| `MAIL_SERVER`         |          | SMTP server for transactional email               |
| `MAIL_PORT`           |          | SMTP port (default `1025`)                        |
| `MEDIA_ROOT`          |          | Path for uploaded files (default `/app/media`)    |
| `SITE_NAME`           |          | Site display name (default `OpenBlog`)            |
