"""Alembic environment configuration.

Reads ``DATABASE_URL`` from the environment (via python-dotenv) so migrations
can run without a running Flask application.  All models are imported via
``backend.models`` to ensure their metadata is registered before autogenerate
inspects the schema.

Usage
-----
Offline (generates SQL, no live DB required)::

    alembic upgrade head --sql

Online (applies directly to the database)::

    alembic upgrade head
    alembic downgrade -1
    alembic revision --autogenerate -m "describe the change"
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Load .env / .env.local so DATABASE_URL is available when running from host.
load_dotenv()

# ── Import all models so their tables are registered in the metadata ───────────
# This is what makes autogenerate work: every model must be imported before
# target_metadata is passed to context.configure().
import backend.models  # noqa: F401 E402  (side-effect import after load_dotenv)
from backend.extensions import db  # noqa: E402

# Alembic Config object (access to alembic.ini values)
config = context.config

# Set up Python logging from the ini file section [loggers] etc.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata Alembic will diff against the live DB.
target_metadata = db.metadata


# ── Database URL ───────────────────────────────────────────────────────────────

def get_url() -> str:
    """Return DATABASE_URL from the environment.

    Raises a clear error if it is missing so developers know exactly what to fix.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set.\n"
            "Copy .env.example to .env and fill in the value, or export it directly."
        )
    return url


# ── Offline mode ──────────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without connecting to the database."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ───────────────────────────────────────────────────────────────

def run_migrations_online() -> None:
    """Apply migrations directly to the database via a live connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
