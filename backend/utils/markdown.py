"""Markdown → sanitised HTML pipeline with Redis caching.

Rendering uses Python-Markdown with fenced_code + tables extensions.
The output is then scrubbed with nh3 (Rust-backed, actively maintained)
to prevent XSS.  nh3 replaced the EOL ``bleach`` library (retired Nov 2023).

Caching
-------
The rendered HTML for a post is stored at ``post:{id}:html`` in Redis with
no TTL (the post's content rarely changes).  Call ``invalidate_html_cache``
whenever ``Post.markdown_body`` is updated.
"""

from __future__ import annotations

import math
import re

import markdown as _md
import nh3
from flask import current_app

_EXTENSIONS = ["fenced_code", "tables"]

# Tags produced by Python-Markdown with the above extensions.
# nh3 requires a frozenset (or set) instead of bleach's list.
_ALLOWED_TAGS: frozenset[str] = frozenset(
    [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "br",
        "hr",
        "strong",
        "em",
        "del",
        "s",
        "code",
        "pre",
        "blockquote",
        "ul",
        "ol",
        "li",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    ]
)

# nh3 attributes map: each tag maps to a set of allowed attribute names.
# NOTE: "rel" must NOT appear here — nh3 owns the rel attribute on <a> tags
# and injects "noopener noreferrer" automatically via its link_rel parameter.
# Attempting to include "rel" in this dict causes a PanicException at runtime.
_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "td": {"align"},
    "th": {"align"},
}

_CACHE_KEY = "post:{id}:html"

# Rough estimate: average English word is ~5 chars; words/min reading speed.
_WPM = 200


def render_markdown(text: str) -> str:
    """Render *text* as Markdown and return sanitised HTML."""
    md = _md.Markdown(extensions=_EXTENSIONS)
    raw_html = md.convert(text)
    # nh3.clean() always strips disallowed tags (equivalent to bleach strip=True).
    return nh3.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
    )


def reading_time_minutes(text: str) -> int:
    """Return estimated reading time in minutes (minimum 1)."""
    word_count = len(re.findall(r"\S+", text))
    return max(1, math.ceil(word_count / _WPM))


def get_rendered_html(post_id: int, markdown_body: str) -> str:
    """Return cached HTML for *post_id*, rendering and caching if absent."""
    redis = current_app.extensions["redis"]
    key = _CACHE_KEY.format(id=post_id)
    cached = redis.get(key)
    if cached:
        return cached
    html = render_markdown(markdown_body)
    # 24-hour TTL as a backstop in case invalidate_html_cache() is never
    # called (e.g. a crash between delete and cache invalidation).
    redis.set(key, html, ex=86400)
    return html


def invalidate_html_cache(post_id: int) -> None:
    """Delete the cached HTML for *post_id* so the next request re-renders it."""
    redis = current_app.extensions["redis"]
    redis.delete(_CACHE_KEY.format(id=post_id))
