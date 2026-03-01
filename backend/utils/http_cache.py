"""HTTP caching helpers — ETag / Last-Modified / conditional GET.

Usage in a route::

    from flask import request
    from backend.utils.http_cache import compute_etag, make_conditional_response

    channel, items, last_modified = feed_svc.get_global_feed()
    body = render_template("feeds/rss.xml", channel=channel, items=items)
    etag = compute_etag("feed", "global", last_modified, len(items))
    return make_conditional_response(
        request, body, "application/rss+xml",
        etag, last_modified, "public, max-age=300",
    )
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import make_response
from flask.wrappers import Request, Response
from werkzeug.http import http_date, parse_date

# ── Date helpers ────────────────────────────────────────────────────────────────


def format_http_date(dt: datetime) -> str:
    """Format *dt* as an RFC 1123 HTTP-date string (always UTC).

    Example: ``"Sun, 01 Jan 2023 12:00:00 GMT"``
    """
    # Ensure UTC before converting to POSIX timestamp.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return http_date(dt.timestamp())


def parse_http_date(value: str) -> datetime | None:
    """Parse an RFC 1123 (or RFC 850 / asctime) date string into an aware UTC datetime.

    Returns ``None`` on any parse error so callers can treat a bad header as absent.
    """
    dt = parse_date(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# ── ETag construction ──────────────────────────────────────────────────────────


def compute_etag(kind: str, scope: str, last_modified: datetime, count: int) -> str:
    """Return a weak ETag for a feed or sitemap.

    Format: ``W/"<kind>-<scope>-<unix_ts>-<count>"``

    *kind*   – resource family, e.g. ``"feed"`` or ``"sitemap"``
    *scope*  – differentiates instances: ``"global"``, a tag slug, a username, …
    *last_modified* – UTC datetime of the most recently changed item
    *count*  – number of items in the collection

    The ETag changes whenever:
    - A new post is published (count increases, timestamp advances).
    - An existing post is edited / a new version is accepted (timestamp advances).
    - A previously published post is unpublished (count decreases).
    """
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=UTC)
    ts = int(last_modified.timestamp())
    # Sanitise scope: replace characters that are illegal in ETag quoted-strings.
    safe_scope = scope.replace('"', "").replace("\\", "").replace(" ", "_")
    return f'W/"{kind}-{safe_scope}-{ts}-{count}"'


# ── Conditional response ───────────────────────────────────────────────────────


def _build_304(etag: str, lm_str: str, cache_control: str) -> Response:
    """Return a minimal 304 Not Modified response with the required caching headers."""
    resp = make_response("", 304)
    resp.headers["ETag"] = etag
    resp.headers["Last-Modified"] = lm_str
    resp.headers["Cache-Control"] = cache_control
    return resp


def make_conditional_response(
    req: Request,
    body: str,
    content_type: str,
    etag: str,
    last_modified: datetime,
    cache_control: str,
) -> Response:
    """Build a full response respecting HTTP conditional-GET semantics.

    Headers set on *both* 200 and 304 responses:
      ``ETag``, ``Last-Modified``, ``Cache-Control``

    Additional header on 200 responses only:
      ``Content-Type`` (with ``; charset=utf-8`` appended automatically)

    304 is returned when:
    - ``If-None-Match`` matches the etag (weak comparison), OR
    - ``If-Modified-Since`` >= last_modified (no changes since client's copy).

    Parameters
    ----------
    req:
        The current Flask ``request`` object.
    body:
        The rendered response body (XML or plain text string).
    content_type:
        MIME type without charset, e.g. ``"application/rss+xml"``.
    etag:
        Weak ETag string produced by :func:`compute_etag`.
    last_modified:
        UTC-aware datetime representing the content's freshness timestamp.
    cache_control:
        ``Cache-Control`` header value, e.g. ``"public, max-age=300"``.
    """
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=UTC)
    lm_str = format_http_date(last_modified)

    # ── If-None-Match check (strong + weak ETag comparison) ────────────────
    inm = req.headers.get("If-None-Match", "")
    if inm:
        # RFC 7232 §3.2: strip outer whitespace, split on comma, weak-strip.
        inm_values = {v.strip() for v in inm.split(",")}

        # Accept both W/"..." and "..." forms from client.
        def _strip_weak(e: str) -> str:
            return e[2:] if e.startswith("W/") else e

        etag_bare = _strip_weak(etag)
        if "*" in inm_values or any(_strip_weak(v) == etag_bare for v in inm_values):
            return _build_304(etag, lm_str, cache_control)

    # ── If-Modified-Since check ────────────────────────────────────────────
    ims_hdr = req.headers.get("If-Modified-Since", "")
    if ims_hdr:
        ims_dt = parse_http_date(ims_hdr)
        # HTTP dates have 1-second granularity; strip microseconds so that a
        # round-tripped timestamp (serialize → parse) still compares equal.
        lm_sec = last_modified.replace(microsecond=0)
        if ims_dt is not None and lm_sec <= ims_dt:
            return _build_304(etag, lm_str, cache_control)

    # ── 200 response ───────────────────────────────────────────────────────
    resp = make_response(body, 200)
    resp.headers["Content-Type"] = f"{content_type}; charset=utf-8"
    resp.headers["ETag"] = etag
    resp.headers["Last-Modified"] = lm_str
    resp.headers["Cache-Control"] = cache_control
    return resp
