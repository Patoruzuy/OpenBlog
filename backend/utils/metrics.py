"""Prometheus metrics for OpenBlog.

All metric objects are module-level singletons so any service module can

    from backend.utils import metrics
    metrics.user_registrations.inc()

without risk of circular imports (this module only imports from
``prometheus_client`` / ``prometheus_flask_exporter``).

HTTP-level request metrics (latency, status counts) are added automatically
by :class:`PrometheusMetrics` from ``prometheus-flask-exporter``.  Business
and infrastructure metrics are declared below.

Initialisation
--------------
Call ``init_metrics(app)`` once per Flask app instance.  Guarded by the
``METRICS_ENABLED`` config flag (``True`` by default; ``False`` for
:class:`TestingConfig`) so the test suite stays lightweight.
"""

from __future__ import annotations

import time

from prometheus_client import Counter, Histogram, Info
from prometheus_flask_exporter import PrometheusMetrics

# ── Flask HTTP metrics (populated by init_metrics) ────────────────────────────
_flask_metrics: PrometheusMetrics | None = None

# ── Business counters ─────────────────────────────────────────────────────────

posts_created = Counter(
    "openblog_posts_created_total",
    "Total blog posts created.",
)
posts_published = Counter(
    "openblog_posts_published_total",
    "Total blog posts transitioned to published status.",
)

user_registrations = Counter(
    "openblog_user_registrations_total",
    "Total successful user registrations.",
)
user_logins = Counter(
    "openblog_user_logins_total",
    "Total login attempts, labelled by outcome.",
    ["outcome"],  # "success" | "failure"
)

revisions_submitted = Counter(
    "openblog_revisions_submitted_total",
    "Total revision proposals submitted by contributors.",
)
revisions_accepted = Counter(
    "openblog_revisions_accepted_total",
    "Total revisions accepted by reviewers.",
)
revisions_rejected = Counter(
    "openblog_revisions_rejected_total",
    "Total revisions rejected by reviewers.",
)

comments_created = Counter(
    "openblog_comments_created_total",
    "Total comments created.",
)
search_queries = Counter(
    "openblog_search_queries_total",
    "Total full-text search queries executed (empty queries excluded).",
)
bookmarks_created = Counter(
    "openblog_bookmarks_created_total",
    "Total bookmark additions.",
)

# ── Celery task metrics ───────────────────────────────────────────────────────

celery_tasks_total = Counter(
    "openblog_celery_tasks_total",
    "Total Celery task executions, labelled by task name and outcome.",
    ["task_name", "status"],  # status: success | failure | retry
)
celery_task_duration_seconds = Histogram(
    "openblog_celery_task_duration_seconds",
    "Celery task wall-clock duration in seconds.",
    ["task_name"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

# ── Database query metrics ────────────────────────────────────────────────────

db_query_duration_seconds = Histogram(
    "openblog_db_query_duration_seconds",
    "SQLAlchemy query duration in seconds.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# ── Build / runtime info ──────────────────────────────────────────────────────

build_info = Info("openblog_build", "OpenBlog build metadata.")


# ── Initialisation ────────────────────────────────────────────────────────────


def init_metrics(app) -> PrometheusMetrics:  # type: ignore[return]
    """Attach Prometheus HTTP metrics to *app* and register infrastructure hooks.

    - Exposes the ``/metrics`` scrape endpoint.
    - Groups HTTP metrics by Flask endpoint name (not raw path) to prevent
      unbounded cardinality from slug-based routes like ``/posts/<slug>``.
    - Registers SQLAlchemy query-timing event hooks.
    - Registers Celery task lifecycle signal handlers.

    Safe to call more than once; subsequent calls are no-ops.
    """
    global _flask_metrics
    if _flask_metrics is not None:
        return _flask_metrics  # type: ignore[return-value]

    flask_m = PrometheusMetrics(
        app,
        group_by="endpoint",
        default_labels={"env": app.config.get("ENV", "development")},
    )
    flask_m.info("app_info", "OpenBlog application metadata", version="0.1.0")
    _flask_metrics = flask_m

    build_info.info({"version": "0.1.0", "env": app.config.get("ENV", "development")})

    _register_sqlalchemy_hooks(app)
    _register_celery_signals()
    return flask_m


def _register_sqlalchemy_hooks(app) -> None:  # type: ignore[return]
    """Time every SQLAlchemy cursor execution and record the duration."""
    from sqlalchemy import event  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415

    # Flask-SQLAlchemy creates the engine lazily; pushing an app context here
    # ensures the real engine exists before we attach listeners.
    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info["_qstart"] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        start = conn.info.pop("_qstart", None)
        if start is not None:
            db_query_duration_seconds.observe(time.perf_counter() - start)


def _register_celery_signals() -> None:
    """Hook Celery task lifecycle signals into task-level metrics counters."""
    from celery.signals import (  # noqa: PLC0415
        task_failure,
        task_postrun,
        task_prerun,
        task_retry,
    )

    @task_prerun.connect(weak=False)
    def _on_prerun(task_id, task, *args, **kwargs):  # noqa: ANN001
        task.request["_metrics_start"] = time.perf_counter()

    @task_postrun.connect(weak=False)
    def _on_postrun(task_id, task, retval, state, *args, **kwargs):  # noqa: ANN001
        start = task.request.get("_metrics_start")
        elapsed = time.perf_counter() - start if start is not None else 0.0
        celery_task_duration_seconds.labels(task_name=task.name).observe(elapsed)
        celery_tasks_total.labels(task_name=task.name, status="success").inc()

    @task_failure.connect(weak=False)
    def _on_failure(task_id, exception, traceback, sender, *args, **kwargs):  # noqa: ANN001
        celery_tasks_total.labels(task_name=sender.name, status="failure").inc()

    @task_retry.connect(weak=False)
    def _on_retry(request, reason, einfo, *args, **kwargs):  # noqa: ANN001
        celery_tasks_total.labels(task_name=request.task, status="retry").inc()
