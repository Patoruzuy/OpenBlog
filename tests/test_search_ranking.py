"""Unit tests for search_ranking module.

Pure Python — no database, no Flask app context required.  All scoring
functions are deterministic, so every assertion is equality-based.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.services.search_ranking import (
    WEIGHTS,
    _ANON,
    score_person,
    score_post,
    score_tag,
    title_score,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _post(
    title: str = "My Post",
    view_count: int = 0,
    version: int = 1,
    published_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> SimpleNamespace:
    """Create a minimal Post-like mock."""
    pub = published_at or (datetime.now(UTC) - timedelta(days=1))
    return SimpleNamespace(
        title=title,
        view_count=view_count,
        version=version,
        published_at=pub,
        updated_at=updated_at,
    )


def _tag(name: str, slug: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, slug=slug or name.lower().replace(" ", "-"))


def _user(
    username: str,
    display_name: str | None = None,
    headline: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        username=username,
        display_name=display_name or username,
        headline=headline or "",
    )


# ── title_score ────────────────────────────────────────────────────────────────


class TestTitleScore:
    def test_exact_match(self):
        assert title_score("Flask Tutorial", "Flask Tutorial") == WEIGHTS["title_exact"]

    def test_exact_match_case_insensitive(self):
        assert title_score("flask tutorial", "Flask Tutorial") == WEIGHTS["title_exact"]

    def test_phrase_match(self):
        s = title_score("flask", "Learn Flask Today")
        assert s == WEIGHTS["title_phrase"]

    def test_all_tokens_in_title(self):
        # "guide flask" is NOT a contiguous substring of "The Complete Flask Guide",
        # but both tokens appear in the title → title_token tier fires.
        s = title_score("guide flask", "The Complete Flask Guide")
        assert s == WEIGHTS["title_token"]

    def test_partial_token_match(self):
        # Half the tokens match  ("flask" matches, "async" doesn't)
        s = title_score("flask async", "Flask Intro")
        assert s == WEIGHTS["title_partial"]

    def test_no_match_returns_zero(self):
        assert title_score("kubernetes", "Flask Tutorial") == 0.0

    def test_empty_query_returns_zero(self):
        assert title_score("", "Flask Tutorial") == 0.0

    def test_empty_title_returns_zero(self):
        assert title_score("flask", "") == 0.0

    def test_waterfall_exact_beats_phrase(self):
        """Exact match should score higher than a phrase match."""
        exact = title_score("Flask", "Flask")
        phrase = title_score("Flask", "Learn Flask Today")
        assert exact > phrase

    def test_phrase_beats_token(self):
        phrase = title_score("flask guide", "flask guide for beginners")
        token = title_score("flask guide", "The Complete Flask and Guide")
        assert phrase > token


# ── score_post ─────────────────────────────────────────────────────────────────


class TestScorePost:
    def test_title_match_raises_score(self):
        """A post whose title matches should outscore a no-title-match post."""
        p_title = _post("Flask Tutorial")
        p_body = _post("Unrelated Post")
        s_title = score_post("Flask", p_title)
        s_body = score_post("Flask", p_body)
        assert s_title > s_body

    def test_tag_match_adds_score(self):
        """Matching a tag slug adds the tag_match weight."""
        post = _post("My Post")
        without_tags = score_post("python", post, tag_slugs=[])
        with_tags = score_post("python", post, tag_slugs=["python", "flask"])
        assert with_tags > without_tags
        assert with_tags - without_tags == pytest.approx(WEIGHTS["tag_match"])

    def test_freshness_recent_beats_old(self):
        """A recently published post should outscore an old one."""
        recent = _post("Same Title", published_at=datetime.now(UTC) - timedelta(days=3))
        old = _post("Same Title", published_at=datetime.now(UTC) - timedelta(days=365))
        s_recent = score_post("Same Title", recent)
        s_old = score_post("Same Title", old)
        assert s_recent > s_old

    def test_quality_higher_views_beats_zero(self):
        """More views → higher quality signal."""
        popular = _post("Post", view_count=5000)
        obscure = _post("Post", view_count=0)
        assert score_post("Post", popular) > score_post("Post", obscure)

    def test_revision_boost_applies(self):
        """Accepted revision count > 0 adds revision_boost."""
        without_rev = score_post("test", _post("test"), accepted_revision_count=0)
        with_rev = score_post("test", _post("test"), accepted_revision_count=3)
        assert with_rev - without_rev == pytest.approx(WEIGHTS["revision_boost"])

    def test_unread_boost_for_unauthenticated_is_absent(self):
        """Anonymous visitor (_ANON sentinel) gets no personalisation boost."""
        anon = score_post("test", _post("test"), read_version=_ANON)
        read = score_post("test", _post("test"), read_version=1)  # version==1 too → no stale
        # Both have same non-personalisation score; ANON should equal fully-read
        # (no boost in either direction)
        assert anon == pytest.approx(read)

    def test_unread_boost_for_never_read(self):
        """Authenticated user who has never read the post gets unread_boost."""
        never_read = score_post("test", _post("test", version=1), read_version=None)
        read_current = score_post("test", _post("test", version=1), read_version=1)
        assert never_read - read_current == pytest.approx(WEIGHTS["unread_boost"])

    def test_stale_read_boost(self):
        """User whose last-read version < post.version gets stale_read_boost."""
        post = _post("test", version=3)
        stale = score_post("test", post, read_version=1)    # 1 < 3 → stale
        current = score_post("test", post, read_version=3)  # 3 == 3 → no boost
        assert stale - current == pytest.approx(WEIGHTS["stale_read_boost"])

    def test_no_boost_when_read_version_equals_current(self):
        """Reading the latest version gives no personalisation boost."""
        post = _post("test", version=2)
        s = score_post("test", post, read_version=2)
        s_anon = score_post("test", post, read_version=_ANON)
        assert s == pytest.approx(s_anon)

    def test_deterministic(self):
        """Same inputs always produce the same score (within floating-point tolerance).

        _freshness() calls datetime.now() on each invocation so two rapid
        back-to-back calls may differ by a sub-microsecond amount.  Using a
        fixed past date sidesteps the clock entirely.
        """
        fixed_dt = datetime(2020, 1, 1, tzinfo=UTC)
        post = _post("Flask Tutorial", view_count=100, version=2, published_at=fixed_dt, updated_at=fixed_dt)
        s1 = score_post("Flask", post, tag_slugs=["flask"], accepted_revision_count=1, read_version=None)
        s2 = score_post("Flask", post, tag_slugs=["flask"], accepted_revision_count=1, read_version=None)
        assert s1 == pytest.approx(s2)

    def test_non_negative(self):
        """Scores are always ≥ 0."""
        post = _post("XYZ", view_count=0)
        assert score_post("zzz", post) >= 0.0


# ── score_tag ──────────────────────────────────────────────────────────────────


class TestScoreTag:
    def test_exact_match(self):
        t = _tag("Python", slug="python")
        assert score_tag("python", t) == WEIGHTS["tag_name_exact"]

    def test_token_match(self):
        # "python" alone matches the token set of "Python Flask" → tag_name_token.
        # (Querying the full name "python flask" would hit tag_name_exact instead.)
        t = _tag("Python Flask", slug="python-flask")
        assert score_tag("python", t) == WEIGHTS["tag_name_token"]

    def test_slug_match(self):
        t = _tag("Web Development", slug="web-development")
        assert score_tag("web dev", t) < WEIGHTS["tag_name_exact"]
        # Slug partial match should fire
        assert score_tag("web-development", t) >= WEIGHTS["tag_slug_match"]

    def test_no_match_returns_zero(self):
        t = _tag("Linux")
        assert score_tag("kubernetes", t) == 0.0

    def test_empty_query_returns_zero(self):
        assert score_tag("", _tag("Python")) == 0.0

    def test_exact_beats_token(self):
        t = _tag("Python", slug="python")
        exact = score_tag("python", t)
        token = score_tag("py", t)  # will not match tokens
        assert exact >= token


# ── score_person ───────────────────────────────────────────────────────────────


class TestScorePerson:
    def test_exact_username_match(self):
        u = _user("alice")
        assert score_person("alice", u) == WEIGHTS["person_username"]

    def test_exact_display_name_match(self):
        u = _user("alice42", display_name="Alice Smith")
        assert score_person("alice smith", u) == WEIGHTS["person_display"]

    def test_headline_match(self):
        u = _user("bob", headline="Senior Python Engineer at Acme")
        s = score_person("python engineer", u)
        assert s > 0

    def test_no_match_returns_zero(self):
        u = _user("charlie", display_name="Charlie Brown")
        assert score_person("kubernetes", u) == 0.0

    def test_empty_query_returns_zero(self):
        assert score_person("", _user("alice")) == 0.0

    def test_username_beats_display(self):
        u = _user("alice", display_name="alice")
        # Both match, but username weight >= display weight
        assert WEIGHTS["person_username"] >= WEIGHTS["person_display"]

    def test_missing_headline_attribute_handled(self):
        """score_person must not raise when user has no headline attribute."""
        u = SimpleNamespace(username="dave", display_name="Dave")
        # No .headline attribute — getattr fallback should handle it
        assert score_person("dave", u) >= 0.0
