"""Unit tests for search_ranking module.

Pure Python — no database, no Flask app context required.  All scoring
functions are deterministic, so every assertion is equality-based.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.services.search_ranking import (
    _ANON,
    WEIGHTS,
    _clamp,
    _freshness,
    _quality,
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
        read = score_post(
            "test", _post("test"), read_version=1
        )  # version==1 too → no stale
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
        stale = score_post("test", post, read_version=1)  # 1 < 3 → stale
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
        post = _post(
            "Flask Tutorial",
            view_count=100,
            version=2,
            published_at=fixed_dt,
            updated_at=fixed_dt,
        )
        s1 = score_post(
            "Flask",
            post,
            tag_slugs=["flask"],
            accepted_revision_count=1,
            read_version=None,
        )
        s2 = score_post(
            "Flask",
            post,
            tag_slugs=["flask"],
            accepted_revision_count=1,
            read_version=None,
        )
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
        # Both match, but username weight >= display weight
        assert WEIGHTS["person_username"] >= WEIGHTS["person_display"]

    def test_missing_headline_attribute_handled(self):
        """score_person must not raise when user has no headline attribute."""
        u = SimpleNamespace(username="dave", display_name="Dave")
        # No .headline attribute — getattr fallback should handle it
        assert score_person("dave", u) >= 0.0


# ── Normalisation / edge-case safety ──────────────────────────────────────────


class TestNormalisationEdgeCases:
    """Verify none of the normalisation helpers can produce NaN, ±Inf, or
    values outside [0, 1], even for degenerate inputs."""

    # _clamp
    def test_clamp_negative_to_zero(self):
        assert _clamp(-99.0) == 0.0

    def test_clamp_over_one_to_one(self):
        assert _clamp(5.0) == 1.0

    def test_clamp_midrange_identity(self):
        assert _clamp(0.7) == pytest.approx(0.7)

    # _freshness
    def test_freshness_none_returns_zero(self):
        """No published_at → freshness = 0, not an exception or NaN."""
        assert _freshness(None) == 0.0

    def test_freshness_future_clamped(self):
        """A timestamp in the future must not return > 1."""
        future = datetime.now(UTC) + timedelta(days=30)
        assert _freshness(future) <= 1.0

    def test_freshness_today_near_one(self):
        assert _freshness(datetime.now(UTC)) == pytest.approx(1.0, abs=1e-3)

    def test_freshness_naive_datetime_handled(self):
        """Timezone-naive datetimes should not raise."""
        naive = datetime.now() - timedelta(days=10)
        result = _freshness(naive)
        assert 0.0 <= result <= 1.0

    # _quality
    def test_quality_zero_views_returns_zero(self):
        assert _quality(0) == 0.0

    def test_quality_negative_views_safe(self):
        """Negative view-count must not produce a negative or NaN score."""
        assert _quality(-100) == 0.0

    def test_quality_large_views_capped_at_one(self):
        assert _quality(10_000_000) == pytest.approx(1.0, abs=1e-3)

    def test_quality_in_unit_interval(self):
        for v in (0, 1, 50, 500, 5000, 10_000):
            assert 0.0 <= _quality(v) <= 1.0

    # score_post edge cases
    def test_score_post_empty_query(self):
        """Empty query → title score 0 but freshness/quality still contribute."""
        post = _post("Flask Tutorial")
        score = score_post("", post)
        assert score >= 0.0

    def test_score_post_none_title_safe(self):
        post = SimpleNamespace(
            title=None,
            view_count=0,
            version=1,
            published_at=None,
            updated_at=None,
        )
        assert score_post("python", post) >= 0.0

    def test_score_post_is_finite(self):
        """Score must never be NaN or Inf."""
        import math as _math

        post = _post("Flask", view_count=999_999_999)
        s = score_post("Flask", post, tag_slugs=["flask"], accepted_revision_count=99)
        assert _math.isfinite(s)


# ── Waterfall monotonicity ─────────────────────────────────────────────────────


class TestWaterfallMonotonicity:
    """The WEIGHTS ordering for title tiers must be strictly decreasing.

    This class also asserts that *title_score()* actually returns each tier's
    weight for a crafted input that isolates exactly that tier.
    """

    def test_weights_strictly_ordered(self):
        """Invariant: exact > phrase > token > partial > 0."""
        assert WEIGHTS["title_exact"] > WEIGHTS["title_phrase"]
        assert WEIGHTS["title_phrase"] > WEIGHTS["title_token"]
        assert WEIGHTS["title_token"] > WEIGHTS["title_partial"]
        assert WEIGHTS["title_partial"] > 0.0

    def test_exact_tier(self):
        # Query == title (case-normalised)
        assert title_score("flask", "Flask") == WEIGHTS["title_exact"]

    def test_phrase_tier(self):
        # Query is a substring of title but not equal
        assert title_score("flask", "Learn Flask Today") == WEIGHTS["title_phrase"]

    def test_token_tier(self):
        # All tokens match but query is NOT a contiguous substring
        # "guide flask" appears reversed in the title, so phrase test fails
        assert (
            title_score("guide flask", "The Complete Flask Guide")
            == WEIGHTS["title_token"]
        )

    def test_partial_tier(self):
        # Only half the tokens match: "flask" ✓, "async" ✗
        assert title_score("flask async", "Flask Intro") == WEIGHTS["title_partial"]

    def test_no_match_tier(self):
        assert title_score("kubernetes", "Flask Tutorial") == 0.0

    def test_each_tier_strictly_less_than_previous(self):
        """Scores returned for canonical examples preserve the ordering."""
        s_exact = title_score("flask", "Flask")
        s_phrase = title_score("flask", "Learn Flask Today")
        s_token = title_score("guide flask", "The Complete Flask Guide")
        s_partial = title_score("flask async", "Flask Intro")
        s_none = title_score("kubernetes", "Flask Tutorial")
        assert s_exact > s_phrase > s_token > s_partial > s_none


# ── Personalisation bounds ─────────────────────────────────────────────────────


class TestPersonalisationBounds:
    """Personalisation boosts must never promote a weaker match above a
    stronger one.  Specifically:

    Post A  exact title match + already read (no boost)
    Post B  body-only match (zero title score) + unread (unread_boost)

    A must still rank above B.
    """

    def test_title_exact_read_beats_body_only_unread(self):
        fixed_dt = datetime(2024, 6, 1, tzinfo=UTC)
        post_a = _post(
            "Flask Tutorial",
            view_count=0,
            version=1,
            published_at=fixed_dt,
            updated_at=fixed_dt,
        )
        post_b = _post(
            "Unrelated Title",
            view_count=0,
            version=1,
            published_at=fixed_dt,
            updated_at=fixed_dt,
        )

        # A: exact title match, authenticated user who has read the post
        score_a = score_post("Flask Tutorial", post_a, read_version=1)

        # B: no title match, authenticated user who has never read the post
        score_b = score_post("Flask Tutorial", post_b, read_version=None)

        assert score_a > score_b, (
            f"Expected title-exact ({score_a:.4f}) > body-only+unread ({score_b:.4f})"
        )

    def test_title_match_beats_max_personalisation(self):
        """title_exact alone must exceed the combined personalisation budget."""
        max_personal = WEIGHTS["unread_boost"] + WEIGHTS["stale_read_boost"]
        assert WEIGHTS["title_exact"] > max_personal, (
            "title_exact weight must dominate full personalisation budget "
            f"({WEIGHTS['title_exact']} vs {max_personal})"
        )

    def test_title_partial_beats_unread_boost(self):
        """Even the weakest title signal (partial) must exceed unread_boost alone."""
        assert WEIGHTS["title_partial"] > WEIGHTS["unread_boost"]

    def test_personalisation_mutually_exclusive(self):
        """unread_boost and stale_read_boost can never both fire for the same post.

        unread (None) has no version, so read_version < post.version is
        checked only for int read_version values.
        """
        fixed_dt = datetime(2024, 1, 1, tzinfo=UTC)
        post = _post("Some Post", version=3, published_at=fixed_dt, updated_at=fixed_dt)

        # Case: never-read (None) → only unread_boost, no stale_read_boost
        s_never_read = score_post("test", post, read_version=None)
        # Manual: unread_boost should be present; stale_read_boost should NOT
        s_without_any = score_post("test", post, read_version=3)  # up to date, no boost
        s_with_stale = score_post(
            "test", post, read_version=1
        )  # stale, only stale_boost

        assert s_never_read - s_without_any == pytest.approx(WEIGHTS["unread_boost"])
        assert s_with_stale - s_without_any == pytest.approx(
            WEIGHTS["stale_read_boost"]
        )
        # Never-read and stale are independent boosts; they can't share a post
        assert (
            s_never_read != s_with_stale
        )  # different magnitudes confirms independence


# ── Read-version mapping contract ─────────────────────────────────────────────


class TestReadVersionMappingContract:
    """Exhaustive tri-state contract for the read_version parameter.

    _ANON → no personalisation signal applied (anonymous request)
    None  → unread_boost added, stale_read_boost NOT added
    int   → if < post.version: stale_read_boost, else no boost
    """

    def setup_method(self):
        fixed_dt = datetime(2023, 1, 1, tzinfo=UTC)
        self.post = _post(
            "Contract Test", version=5, published_at=fixed_dt, updated_at=fixed_dt
        )
        self.query = "contract test"

    def _base(self, read_version):
        return score_post(self.query, self.post, read_version=read_version)

    def test_anon_has_no_personalisation(self):
        """_ANON sentinel: same score as a fully-current authenticated reader."""
        s_anon = self._base(_ANON)
        s_current = self._base(5)  # read the latest version, no boost
        assert s_anon == pytest.approx(s_current)

    def test_none_adds_exactly_unread_boost(self):
        s_never = self._base(None)
        s_current = self._base(5)
        assert s_never - s_current == pytest.approx(WEIGHTS["unread_boost"])

    def test_stale_version_adds_exactly_stale_boost(self):
        s_stale = self._base(1)  # last read v1, current is v5
        s_current = self._base(5)
        assert s_stale - s_current == pytest.approx(WEIGHTS["stale_read_boost"])

    def test_current_version_adds_no_boost(self):
        s_current = self._base(5)
        s_anon = self._base(_ANON)
        assert s_current == pytest.approx(s_anon)

    def test_future_read_version_adds_no_boost(self):
        """read_version > post.version shouldn't crash or add stale_boost."""
        s_future = self._base(99)
        s_current = self._base(5)
        assert s_future == pytest.approx(s_current)

    def test_unread_boost_greater_than_zero(self):
        assert WEIGHTS["unread_boost"] > 0

    def test_stale_read_boost_greater_than_zero(self):
        assert WEIGHTS["stale_read_boost"] > 0
