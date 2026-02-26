"""Shared input validation helpers.

Used by services to validate user-supplied data before persisting it.
"""

from __future__ import annotations

from urllib.parse import urlparse


def validate_url(url: str | None, *, field: str = "URL") -> str | None:
    """Validate that *url* uses http or https.

    Parameters
    ----------
    url:
        URL string to validate, or ``None`` (passthrough).
    field:
        Human-readable field name for error messages.

    Returns
    -------
    The original *url* string if valid, or ``None`` if *url* is ``None``.

    Raises
    ------
    ValueError
        If the URL scheme is not ``http`` or ``https``.
    """
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"{field} must use http or https (got {parsed.scheme!r})."
        )
    return url
