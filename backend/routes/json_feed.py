"""JSON Feed v1.1 routes.

Three public feeds:
  GET /feed.json                   — site-wide (all published posts)
  GET /tags/<slug>/feed.json       — posts with a specific tag
  GET /users/<username>/feed.json  — posts by a specific author

All feeds are served as UTF-8 ``application/feed+json`` and support
conditional GET (ETag / If-None-Match and Last-Modified / If-Modified-Since).

Fingerprinting strategy
-----------------------
ETags are computed by :func:`~backend.utils.http_cache.compute_etag` using the
``kind="jfeed"`` prefix, the same *scope* strings used by the RSS routes, the
``last_modified`` timestamp, and the item count.  This mirrors the RSS
fingerprint logic exactly:

- ``last_modified`` derives from the maximum of ``Post.updated_at``,
  ``Post.published_at``, and ``PostVersion.created_at`` across **published-only**
  post IDs — drafts never participate.
- The ETag therefore changes when a new post is published, when an accepted
  revision raises the version timestamp, or when a post is unpublished.

Cache-Control: ``public, max-age=300`` (5 minutes), same as RSS.

Draft / private safety
----------------------
Filtering is enforced at the query level in ``feed_service.py``.  Private-
profile author feeds return 404 with no caching headers.
"""

from __future__ import annotations

import json

from flask import Blueprint, Response, abort, request

import backend.services.feed_service as feed_svc
from backend.utils.http_cache import compute_etag, make_conditional_response

json_feed_bp = Blueprint("json_feed", __name__)

_FEED_CC = "public, max-age=300"
_FEED_CT = "application/feed+json"


def _build_body(meta: dict, items: list) -> str:
    """Serialise a JSON Feed document to a UTF-8 string."""
    doc = {**meta, "items": items}
    return json.dumps(doc, ensure_ascii=False, indent=2)


@json_feed_bp.get("/feed.json")
def global_json_feed() -> Response:
    """Site-wide JSON Feed."""
    meta, items, last_modified = feed_svc.get_global_json_feed()
    body = _build_body(meta, items)
    etag = compute_etag("jfeed", "global", last_modified, len(items))
    return make_conditional_response(
        request, body, _FEED_CT, etag, last_modified, _FEED_CC
    )


@json_feed_bp.get("/tags/<slug>/feed.json")
def tag_json_feed(slug: str) -> Response:
    """JSON Feed filtered to posts tagged *slug*."""
    result = feed_svc.get_tag_json_feed(slug)
    if result is None:
        abort(404)
    meta, items, last_modified = result
    body = _build_body(meta, items)
    etag = compute_etag("jfeed", f"tag-{slug}", last_modified, len(items))
    return make_conditional_response(
        request, body, _FEED_CT, etag, last_modified, _FEED_CC
    )


@json_feed_bp.get("/users/<username>/feed.json")
def author_json_feed(username: str) -> Response:
    """JSON Feed for posts by *username*.

    Returns 404 if the user does not exist or has a private profile.
    No caching headers are sent for 404 responses.
    """
    result = feed_svc.get_author_json_feed(username)
    if result is None:
        abort(404)
    meta, items, last_modified = result
    body = _build_body(meta, items)
    etag = compute_etag("jfeed", f"author-{username}", last_modified, len(items))
    return make_conditional_response(
        request, body, _FEED_CT, etag, last_modified, _FEED_CC
    )
