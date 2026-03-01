"""RSS feed routes.

Three public feeds:
  GET /feed.xml                   — site-wide (all published posts)
  GET /tags/<slug>/feed.xml       — posts with a specific tag
  GET /users/<username>/feed.xml  — posts by a specific author

All feeds are rendered as UTF-8 RSS 2.0 XML and support conditional GET
(ETag / If-None-Match and Last-Modified / If-Modified-Since).

Cache-Control policy: ``public, max-age=300`` (5 minutes).

Draft posts are *never* included; enforcement is at the query level in
feed_service.py.  Private-profile author feeds return 404 so no caching
headers are sent for non-public resources.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, render_template, request

import backend.services.feed_service as feed_svc
from backend.utils.http_cache import compute_etag, make_conditional_response

feed_bp = Blueprint("feed", __name__)

_FEED_CC = "public, max-age=300"
_FEED_CT = "application/rss+xml"


def _render_feed(channel: dict, items: list) -> str:
    return render_template("feeds/rss.xml", channel=channel, items=items)


@feed_bp.get("/feed.xml")
def global_feed() -> Response:
    """Site-wide RSS feed."""
    channel, items, last_modified = feed_svc.get_global_feed()
    body = _render_feed(channel, items)
    etag = compute_etag("feed", "global", last_modified, len(items))
    return make_conditional_response(
        request, body, _FEED_CT, etag, last_modified, _FEED_CC
    )


@feed_bp.get("/tags/<slug>/feed.xml")
def tag_feed(slug: str) -> Response:
    """RSS feed filtered to posts tagged *slug*."""
    result = feed_svc.get_tag_feed(slug)
    if result is None:
        abort(404)
    channel, items, last_modified = result
    body = _render_feed(channel, items)
    etag = compute_etag("feed", f"tag-{slug}", last_modified, len(items))
    return make_conditional_response(
        request, body, _FEED_CT, etag, last_modified, _FEED_CC
    )


@feed_bp.get("/users/<username>/feed.xml")
def author_feed(username: str) -> Response:
    """RSS feed for posts by *username*.

    Returns 404 if the user does not exist or has a private profile.
    No caching headers are sent for 404 responses.
    """
    result = feed_svc.get_author_feed(username)
    if result is None:
        abort(404)
    channel, items, last_modified = result
    body = _render_feed(channel, items)
    etag = compute_etag("feed", f"author-{username}", last_modified, len(items))
    return make_conditional_response(
        request, body, _FEED_CT, etag, last_modified, _FEED_CC
    )
