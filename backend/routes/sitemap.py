"""Sitemap and robots.txt routes.

GET /sitemap.xml   — XML sitemap of all public URLs (supports conditional GET)
GET /robots.txt    — directives for web crawlers (simple max-age caching)
"""

from __future__ import annotations

from flask import Blueprint, Response, request

import backend.services.sitemap_service as sitemap_svc
from backend.utils.http_cache import (
    compute_etag,
    make_conditional_response,
)
from backend.utils.seo import absolute_url

sitemap_bp = Blueprint("sitemap", __name__)

_SITEMAP_CC = "public, max-age=300"
_ROBOTS_CC = "public, max-age=86400"


@sitemap_bp.get("/sitemap.xml")
def sitemap() -> Response:
    """Return the XML sitemap for all public pages with ETag/Last-Modified support."""
    from flask import render_template  # local to avoid circular at module level

    entries, last_modified = sitemap_svc.build_entries()
    body = render_template("sitemap.xml", entries=entries)
    etag = compute_etag("sitemap", "global", last_modified, len(entries))
    return make_conditional_response(
        request, body, "application/xml", etag, last_modified, _SITEMAP_CC
    )


@sitemap_bp.get("/robots.txt")
def robots() -> Response:
    """Return a robots.txt that allows all crawlers and points to the sitemap."""
    sitemap_url = absolute_url("/sitemap.xml")
    body = f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}\n"
    resp = Response(body, status=200, content_type="text/plain; charset=utf-8")
    resp.headers["Cache-Control"] = _ROBOTS_CC
    return resp
