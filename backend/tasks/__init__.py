"""Celery tasks package.

Importing this package registers all task modules with the shared Celery
instance so the worker discovers them at startup.
"""

import backend.tasks.email  # noqa: F401
import backend.tasks.notifications  # noqa: F401
