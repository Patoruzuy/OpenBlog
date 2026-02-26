"""Markdown → sanitised HTML pipeline with Redis caching.

Rendering uses Python-Markdown with fenced_code + tables extensions.
The output is then scrubbed with bleach to prevent XSS.

Caching
-------
The rendered HTML for a post is stored at ``post:{id}:html`` in Redis with
no TTL (the post's content rarely changes).  Call ``invalidate_html_cache``
whenever ``Post.markdown_body`` is updated.
"""

from __future__ import annotations

import re

import bleach
import markdown as _md
from flask import current_app

_EXTENSIONS = ["fenced_code", "tables"]

# Tags produced by Python-Markdown with the above extensions.
_ALLOWED_TAGS: list[str] = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "del", "s",
    "code", "pre",
    "blockquote",
    "ul", "ol", "li",
    "a",
    "img",
    "table", "thead", "tbody", "tr", "th", "td",
]

_ALLOWED_ATTRS: dict[str, list[str]] = {
    "a":   ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
    "code": ["class"],
    "td":  ["align"],
    "th":  ["align"],
}

_CACHE_KEY = "post:{id}:html"

# Rough estimate: average English word is ~5 chars; words/min reading speed.
_WPM = 200


def render_markdown(text: str) -> str:
    """Render *text* as Markdown and return sanitised HTML."""
    md = _md.Markdown(extensions=_EXTENSIONS)
    raw_html = md.convert(text)
    return bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
    )


def reading_time_minutes(text: str) -> int:
    """Return estimated reading time in minutes (minimum 1)."""
    word_count = len(re.findall(r"\S+", text))
    import math
    return max(1, math.ceil(word_count / _WPM))


def get_rendered_html(post_id: int, markdown_body: str) -> str:
    """Return cached HTML for *post_id*, rendering and caching if absent."""
    redis = current_app.extensions["redis"]
    key = _CACHE_KEY.format(id=post_id)
    cached = redis.get(key)
    if cached:
        return cached
    html = render_markdown(markdown_body)
    redis.set(key, html)
    return html


def invalidate_html_cache(post_id: int) -> None:
    """Delete the cached HTML for *post_id* so the next request re-renders it."""
    redis = current_app.extensions["redis"]
    redis.delete(_CACHE_KEY.format(id=post_id))
