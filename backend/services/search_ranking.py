"""Search ranking heuristic module.

Provides deterministic, tunable weighted scoring for post, tag and user
search candidates.  ALL weights live in ``WEIGHTS``; no numeric constants
appear anywhere else in the codebase.

Scoring model
-------------
  final_score = Σ weight_i × feature_i

Every feature is normalised to [0, 1] before multiplication, so each
weight represents its maximum contribution, and the overall score is
bounded by ``sum(WEIGHTS.values())``.

Usage
-----
  from backend.services.search_ranking import score_post, score_tag, score_person

  s = score_post(
          query,
          post,
          tag_slugs=["python", "flask"],
          accepted_revision_count=2,
          read_version=None,          # "never read by this user"
      )
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

# ── All tunable weights live here ─────────────────────────────────────────────

WEIGHTS: dict[str, float] = {
    # Post title-match signals (waterfall — only the best tier fires)
    "title_exact":       1.00,  # normalised query == normalised title
    "title_phrase":      0.80,  # full query is a substring of title
    "title_token":       0.60,  # ALL query tokens appear in title tokens
    "title_partial":     0.25,  # ≥ half of query tokens appear in title tokens
    # Post content signals
    "tag_match":         0.40,  # ≥ 1 query token matches a tag slug
    # Post quality / freshness signals
    "freshness":         0.30,  # exponential decay (half-life = 60 d)
    "quality":           0.20,  # log-normalised view count
    "revision_boost":    0.20,  # post has ≥ 1 accepted community revision
    # Personalisation signals (applied only for authenticated users)
    "unread_boost":      0.10,  # user has never read this post
    "stale_read_boost":  0.15,  # user's last-read version < post.version
    # Tag scoring
    "tag_name_exact":    1.00,  # query (normalised) == tag name
    "tag_name_token":    0.70,  # all tokens match tag name tokens
    "tag_slug_match":    0.60,  # slug-normalised query matches tag slug
    # Person scoring
    "person_username":   1.00,  # query matches username
    "person_display":    0.80,  # query matches display_name
    "person_headline":   0.40,  # query appears in headline
}

# Freshness half-life: content this many days old scores ~0.5 on freshness.
_FRESHNESS_HALF_LIFE_DAYS: float = 60.0

# Sentinel: indicates the requesting user is anonymous (no personalisation).
# Distinct from ``None`` (which means "authenticated but never read").
_ANON: object = object()


# ── Tokenisation ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Return lower-case alphanumeric tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _slug_norm(text: str) -> str:
    """Convert text to a slug-comparable form."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")


# ── Normalisation helpers ──────────────────────────────────────────────────────

def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _freshness(dt: datetime | None) -> float:
    """Exponential decay in [0, 1].  More recent → closer to 1."""
    if dt is None:
        return 0.0
    now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta_days = max(0.0, (now - dt).total_seconds() / 86_400.0)
    return math.exp(-delta_days * math.log(2.0) / _FRESHNESS_HALF_LIFE_DAYS)


def _quality(view_count: int, ceiling: int = 10_000) -> float:
    """Log-normalised popularity in [0, 1], saturating near *ceiling*."""
    return _clamp(math.log1p(max(0, view_count)) / math.log1p(ceiling))


# ── Title-match scoring ────────────────────────────────────────────────────────

def _title_match_score(query: str, title: str) -> float:
    """Return the best matching title-signal weight (waterfall)."""
    q = query.strip().lower()
    t = title.strip().lower()
    if not q or not t:
        return 0.0

    if q == t:
        return WEIGHTS["title_exact"]
    if q in t:
        return WEIGHTS["title_phrase"]

    q_tok = set(_tokenize(q))
    t_tok = set(_tokenize(t))
    if not q_tok:
        return 0.0

    overlap = q_tok & t_tok
    n_q = len(q_tok)
    if len(overlap) == n_q:
        return WEIGHTS["title_token"]
    if len(overlap) >= max(1, n_q // 2):
        return WEIGHTS["title_partial"]
    return 0.0


# ── Public scoring functions ───────────────────────────────────────────────────

def title_score(query: str, title: str) -> float:
    """Public wrapper: title-relevance score in [0, 1].

    Suitable for lightweight ranking of suggest results where only the title
    is available (no full Post object required).
    """
    return _title_match_score(query, title)


def score_post(
    query: str,
    post: Any,
    *,
    tag_slugs: list[str] | None = None,
    accepted_revision_count: int = 0,
    read_version: Any = _ANON,
) -> float:
    """Return a relevance score for *post* against *query*.

    Parameters
    ----------
    query:
        The user's search query.
    post:
        A ``Post`` ORM instance (reads: ``.title``, ``.updated_at``,
        ``.published_at``, ``.view_count``, ``.version``).
    tag_slugs:
        Pre-fetched list of tag slugs for this post (avoids lazy-loads).
        Pass ``None`` or ``[]`` when tags are unavailable.
    accepted_revision_count:
        Number of accepted revisions for this post.
    read_version:
        * ``_ANON`` sentinel → anonymous visitor (no personalisation).
        * ``None``           → authenticated user, never read this post
                               → ``unread_boost`` applied.
        * ``int``            → authenticated user's last-read version;
                               if < ``post.version`` → ``stale_read_boost``.
    """
    score = 0.0

    # 1. Title match
    title = getattr(post, "title", "") or ""
    score += _title_match_score(query, title)

    # 2. Tag match
    q_tokens = set(_tokenize(query))
    slugs = {s.lower() for s in (tag_slugs or [])}
    if q_tokens and (q_tokens & slugs):
        score += WEIGHTS["tag_match"]

    # 3. Freshness
    ref_dt = getattr(post, "updated_at", None) or getattr(post, "published_at", None)
    score += WEIGHTS["freshness"] * _freshness(ref_dt)

    # 4. Quality
    views = getattr(post, "view_count", 0) or 0
    score += WEIGHTS["quality"] * _quality(views)

    # 5. Revision boost
    if accepted_revision_count > 0:
        score += WEIGHTS["revision_boost"]

    # 6. Personalisation (only for authenticated users)
    if read_version is not _ANON:
        if read_version is None:
            # Authenticated user has never read this post
            score += WEIGHTS["unread_boost"]
        elif isinstance(read_version, int):
            post_version = getattr(post, "version", 1) or 1
            if read_version < post_version:
                score += WEIGHTS["stale_read_boost"]

    return score


def score_tag(query: str, tag: Any) -> float:
    """Return a relevance score for *tag* against *query*.

    Works with any object that has ``.name`` and ``.slug`` attributes,
    including raw SQLAlchemy row tuples accessed by column name.
    """
    q = query.strip().lower()
    if not q:
        return 0.0

    name = (getattr(tag, "name", "") or "").lower()
    slug = (getattr(tag, "slug", "") or "").lower()
    q_slug = _slug_norm(q)

    if q == name:
        return WEIGHTS["tag_name_exact"]

    q_tok = set(_tokenize(q))
    n_tok = set(_tokenize(name))
    if q_tok and q_tok <= n_tok:
        return WEIGHTS["tag_name_token"]

    if q_slug and (q_slug == slug or q_slug in slug):
        return WEIGHTS["tag_slug_match"]

    return 0.0


def score_person(query: str, user: Any) -> float:
    """Return a relevance score for *user* against *query*.

    Works with any object that has ``.username``, ``.display_name`` and
    optionally ``.headline`` attributes (all absent attributes fall back to
    empty strings).
    """
    q = query.strip().lower()
    if not q:
        return 0.0

    username = (getattr(user, "username", "") or "").lower()
    display = (getattr(user, "display_name", "") or "").lower()
    headline = (getattr(user, "headline", "") or "").lower()

    # Exact match on either field
    if q == username:
        return WEIGHTS["person_username"]
    if q == display:
        return WEIGHTS["person_display"]

    score = 0.0
    q_tok = set(_tokenize(q))

    if q_tok and q_tok <= set(_tokenize(username)):
        score = max(score, WEIGHTS["person_username"])
    if q_tok and q_tok <= set(_tokenize(display)):
        score = max(score, WEIGHTS["person_display"])
    if headline and q in headline:
        score = max(score, WEIGHTS["person_headline"])

    return score
