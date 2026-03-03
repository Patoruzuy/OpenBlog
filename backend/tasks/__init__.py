"""Celery tasks package.

Importing this package registers all task modules with the shared Celery
instance so the worker discovers them at startup.
"""

import backend.tasks.ai_reviews  # noqa: F401
import backend.tasks.analytics_explanations  # noqa: F401
import backend.tasks.benchmark_runs  # noqa: F401
import backend.tasks.digests  # noqa: F401
import backend.tasks.email  # noqa: F401
import backend.tasks.notifications  # noqa: F401
