"""Gunicorn production configuration.

Tuned for sync workers on a single server (Hetzner VPS).
Revisit worker_class if SSE / WebSockets are added in a future phase.
"""

import multiprocessing
import os

# ─── Server socket ────────────────────────────────────────────────────────────
bind = "0.0.0.0:8000"
# Ensure gunicorn can resolve 'backend.app' regardless of CWD
chdir = "/app"

# ─── Workers ──────────────────────────────────────────────────────────────────
# Cap at 9 — avoids spawning dozens of workers on WSL2/cloud hosts that report
# many logical CPUs. Raise the ceiling via the WEB_CONCURRENCY env var if needed.
workers = int(
    os.environ.get("WEB_CONCURRENCY", min(multiprocessing.cpu_count() * 2 + 1, 9))
)
worker_class = "sync"
threads = 1

# ─── Timeouts ─────────────────────────────────────────────────────────────────
timeout = 30
graceful_timeout = 20
keepalive = 5

# ─── Logging — stdout/stderr captured by Docker ───────────────────────────────
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'
)

# ─── Performance ──────────────────────────────────────────────────────────────
# Preload app in master process before forking.
# create_app() must NOT open DB connections or ping Redis at import time.
preload_app = True
max_requests = 1000
max_requests_jitter = 100
