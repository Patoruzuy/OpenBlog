"""SEO helpers — absolute URL construction and canonical URL resolution.

Both helpers read ``PUBLIC_BASE_URL`` from the Flask application config.
They are intentionally thin so they can be registered as Jinja2 globals and
called from templates without side-effects.
"""

from __future__ import annotations

from flask import current_app
from flask import request as flask_request


def absolute_url(path_or_url: str) -> str:
    """Return an absolute URL for *path_or_url*.

    If *path_or_url* is already absolute (starts with ``http://`` or
    ``https://``) it is returned unchanged.  Otherwise ``PUBLIC_BASE_URL``
    from the application config is prepended.

    Examples::

        absolute_url("/posts/my-post")
        # → "https://openblog.dev/posts/my-post"

        absolute_url("https://cdn.example.com/img.png")
        # → "https://cdn.example.com/img.png"  (unchanged)
    """
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    base: str = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
    return base + path


def canonical_url(req=None) -> str:  # type: ignore[assignment]
    """Return the canonical absolute URL for the current (or given) request.

    Omits query-string parameters so that paginated / comparison URLs all
    share a single canonical form.

    Pass *req* explicitly when calling outside a request context; it defaults
    to ``flask.request``.

    Examples::

        # In a template:
        {{ canonical_url(request) }}
        # → "https://openblog.dev/posts/my-post"
    """
    r = req if req is not None else flask_request
    base: str = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    return base + r.path
