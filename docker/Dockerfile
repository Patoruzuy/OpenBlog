# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.5 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_CACHE_DIR=/tmp/poetry-cache

RUN pip install "poetry==$POETRY_VERSION"

WORKDIR /app

# Copy only dependency manifests first — maximises Docker layer cache reuse.
# poetry.lock is optional glob (*); if absent, Poetry resolves and creates it.
COPY pyproject.toml poetry.lock* ./

RUN poetry install --only=main --no-ansi && rm -rf "$POETRY_CACHE_DIR"

# Copy translations and compile .po → .mo inside the builder venv.
# Compiling here keeps the runtime image clean (no compile step at start-up).
COPY backend/translations ./backend/translations
RUN .venv/bin/python -m babel.messages.frontend compile -d backend/translations

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

# Non-root user for security
RUN addgroup --system openblog \
    && adduser --system --ingroup openblog --no-create-home openblog

WORKDIR /app

# Copy virtual environment from builder (no Poetry needed at runtime)
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY backend/ ./backend/
COPY alembic.ini ./
COPY gunicorn.conf.py ./

# Overlay the pre-compiled .mo files from the builder stage so the runtime
# image never needs pybabel or a compile step.
COPY --from=builder /app/backend/translations ./backend/translations

# Ensure non-root user owns the app
RUN chown -R openblog:openblog /app

USER openblog

EXPOSE 8000

# gunicorn resolves 'backend.app:create_app()' via PYTHONPATH=/app
CMD ["gunicorn", "-c", "gunicorn.conf.py", "backend.app:create_app()"]
