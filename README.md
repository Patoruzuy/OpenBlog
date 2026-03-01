# OpenBlog

A production-ready developer blog platform with GitHub-style collaborative editing,
full-text search, analytics, Prometheus observability, and RSS/JSON feed support.

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
| `worker` | —                 | Celery async worker (email, analytics)  |
| `beat`   | —                 | Celery beat (scheduled publish, flush)  |

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
- **Notifications** — in-app + email with Redis-backed unread count

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
├── test_*.py                # 1 440+ unit tests
└── integration/             # Real-DB integration tests (opt-in)
docker/
├── Dockerfile               # Multi-stage: builder + non-root runtime
└── nginx/nginx.conf         # Reverse proxy + static file serving
```

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
