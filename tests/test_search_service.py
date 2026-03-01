"""Unit tests for SearchService.

Uses the ``db_session`` fixture (SQLite in-memory, _FakeRedis).
All search tests exercise the SQLite LIKE back-end.
"""

from __future__ import annotations

from backend.extensions import db as _db
from backend.models.portal import IdentityMode, ProfileVisibility, UserPrivacySettings
from backend.services.auth_service import AuthService
from backend.services.post_service import PostService
from backend.services.search_service import SearchService

# ── Shared helpers ────────────────────────────────────────────────────────────

_counter = {"n": 0}


def _unique(prefix: str) -> str:
    _counter["n"] += 1
    return f"{prefix}{_counter['n']}"


def make_author():
    return AuthService.register("author@search.com", "srchauthor", "StrongPass123!!")


def make_post(
    author_id: int, title: str, body: str = "", tags: list[str] | None = None
):
    post = PostService.create(author_id, title, body, tags=tags or [])
    return PostService.publish(post)


def make_public_user(
    username: str, display_name: str | None = None, headline: str | None = None
):
    """Create a user with no privacy settings row (defaults to public/searchable)."""
    u = AuthService.register(f"{username}@test.com", username, "StrongPass123!!")
    if display_name:
        u.display_name = display_name
    if headline:
        u.headline = headline
    _db.session.commit()
    return u


def add_privacy(
    user, visibility: str = "public", searchable: bool = True, mode: str = "public"
):
    """Attach a UserPrivacySettings row to *user* and commit."""
    priv = UserPrivacySettings(
        user_id=user.id,
        profile_visibility=visibility,
        searchable_profile=searchable,
        default_identity_mode=mode,
    )
    _db.session.add(priv)
    _db.session.commit()
    return priv


# ── Empty / whitespace query ───────────────────────────────────────────────────


class TestSearchEmpty:
    def test_empty_string_returns_nothing(self, db_session):  # noqa: ARG002
        _r = SearchService.search("")
        posts, total = _r.posts, _r.post_total
        assert posts == [] and total == 0

    def test_whitespace_only_returns_nothing(self, db_session):  # noqa: ARG002
        _r = SearchService.search("   ")
        posts, total = _r.posts, _r.post_total
        assert posts == [] and total == 0


# ── Title matching ─────────────────────────────────────────────────────────────


class TestSearchTitle:
    def test_exact_title_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Flask Tutorial", "Learn Flask today.")
        _r = SearchService.search("Flask Tutorial")
        posts, total = _r.posts, _r.post_total
        assert total == 1
        assert posts[0].title == "Flask Tutorial"

    def test_partial_title_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Advanced Python Tips")
        _r = SearchService.search("Python")
        total = _r.post_total
        assert total == 1

    def test_case_insensitive_title(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Docker Compose Guide")
        _r = SearchService.search("docker")
        total = _r.post_total
        assert total == 1

    def test_no_match_returns_empty(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Unrelated Post")
        _r = SearchService.search("kubernetes")
        posts, total = _r.posts, _r.post_total
        assert total == 0 and posts == []


# ── Body matching ──────────────────────────────────────────────────────────────


class TestSearchBody:
    def test_body_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "My Post", "This covers async/await in depth.")
        _r = SearchService.search("async")
        total = _r.post_total
        assert total == 1

    def test_body_no_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "My Post", "Completely unrelated content.")
        _r = SearchService.search("microservices")
        total = _r.post_total
        assert total == 0


# ── Tag matching ───────────────────────────────────────────────────────────────


class TestSearchTags:
    def test_tag_name_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "A Post About Stuff", tags=["python", "flask"])
        _r = SearchService.search("flask")
        total = _r.post_total
        assert total == 1

    def test_tag_slug_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Another Post", tags=["machine-learning"])
        _r = SearchService.search("machine")
        total = _r.post_total
        assert total == 1


# ── Draft exclusion ────────────────────────────────────────────────────────────


class TestSearchDraftExclusion:
    def test_draft_not_returned(self, db_session):  # noqa: ARG002
        author = make_author()
        # Create but do NOT publish
        PostService.create(author.id, "Secret Draft Post", "Draft content here")
        _r = SearchService.search("Secret Draft")
        posts, total = _r.posts, _r.post_total
        assert total == 0 and posts == []

    def test_published_returned(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Public Article", "Visible content")
        _r = SearchService.search("Public Article")
        total = _r.post_total
        assert total == 1


# ── Pagination ────────────────────────────────────────────────────────────────


class TestSearchPagination:
    def test_per_page_limits_results(self, db_session):  # noqa: ARG002
        author = make_author()
        for i in range(5):
            make_post(author.id, f"Python Post {i}", "Python content here")
        _r = SearchService.search("Python", page=1, per_page=3)
        posts, total = _r.posts, _r.post_total
        assert total == 5
        assert len(posts) == 3

    def test_page_2_offset(self, db_session):  # noqa: ARG002
        author = make_author()
        for i in range(4):
            make_post(author.id, f"Go Post {i}", "Go content here")
        total = SearchService.search("Go", page=1, per_page=2).post_total
        posts_p2 = SearchService.search("Go", page=2, per_page=2).posts
        assert total == 4
        assert len(posts_p2) == 2

    def test_per_page_clamped_to_100(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Rust Post", "Rust content")
        total = SearchService.search("Rust", per_page=9999).post_total
        assert total == 1


# ── Excerpt helper ────────────────────────────────────────────────────────────


class TestSearchExcerpt:
    def test_short_body_unchanged(self):
        body = "Hello world"
        assert SearchService.excerpt(body, "Hello") == "Hello world"

    def test_hit_centred_in_excerpt(self):
        body = "a " * 60 + "TARGET" + " b" * 60
        result = SearchService.excerpt(body, "TARGET", length=50)
        assert "TARGET" in result

    def test_no_hit_returns_prefix(self):
        body = "x " * 200
        result = SearchService.excerpt(body, "NOTFOUND", length=20)
        assert result.endswith("…")

    def test_ellipsis_added_when_truncated(self):
        body = "a" * 400
        result = SearchService.excerpt(body, "NOTFOUND", length=50)
        assert result.endswith("…")

    def test_no_ellipsis_for_short_body(self):
        body = "Short body"
        result = SearchService.excerpt(body, "NOTFOUND", length=200)
        assert not result.endswith("…")


# ── People search ──────────────────────────────────────────────────────────────


class TestSearchPeopleBasic:
    """SearchService.search() people results respect privacy rules."""

    def test_public_user_no_privacy_row_returned(self, db_session):  # noqa: ARG002
        """User with no privacy settings row defaults to public/searchable."""
        make_public_user("johndoe", display_name="John Doe")
        r = SearchService.search("johndoe")
        assert any(u.username == "johndoe" for u in r.users)
        assert r.user_total >= 1

    def test_match_by_display_name(self, db_session):  # noqa: ARG002
        make_public_user("jsmith42", display_name="Jane Smith")
        r = SearchService.search("Jane Smith")
        assert any(u.username == "jsmith42" for u in r.users)

    def test_match_by_headline(self, db_session):  # noqa: ARG002
        make_public_user("devjane", headline="Python developer at ACME")
        r = SearchService.search("Python developer")
        assert any(u.username == "devjane" for u in r.users)

    def test_match_by_username(self, db_session):  # noqa: ARG002
        make_public_user("rustacean99")
        r = SearchService.search("rustacean")
        assert any(u.username == "rustacean99" for u in r.users)

    def test_no_match_returns_empty_users(self, db_session):  # noqa: ARG002
        make_public_user("someuser")
        r = SearchService.search("zzznomatch")
        assert r.users == []
        assert r.user_total == 0


class TestSearchPeoplePrivacy:
    """Only public and searchable profiles appear in search results."""

    def test_private_profile_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("privateuser")
        add_privacy(u, visibility=ProfileVisibility.private.value)
        r = SearchService.search("privateuser")
        assert r.users == []

    def test_members_only_profile_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("membersonly")
        add_privacy(u, visibility=ProfileVisibility.members.value)
        r = SearchService.search("membersonly")
        assert r.users == []

    def test_unsearchable_profile_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("nosearchme")
        add_privacy(u, visibility=ProfileVisibility.public.value, searchable=False)
        r = SearchService.search("nosearchme")
        assert r.users == []

    def test_anonymous_identity_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("anonuser")
        add_privacy(
            u,
            visibility=ProfileVisibility.public.value,
            searchable=True,
            mode=IdentityMode.anonymous.value,
        )
        r = SearchService.search("anonuser")
        assert r.users == []

    def test_public_with_privacy_row_returned(self, db_session):  # noqa: ARG002
        u = make_public_user("fullpublic")
        add_privacy(
            u,
            visibility=ProfileVisibility.public.value,
            searchable=True,
            mode=IdentityMode.public.value,
        )
        r = SearchService.search("fullpublic")
        assert any(uu.username == "fullpublic" for uu in r.users)

    def test_pseudonymous_identity_returned(self, db_session):  # noqa: ARG002
        """Pseudonymous profiles are still searchable — only anonymous ones are hidden."""
        u = make_public_user("pseudouser")
        add_privacy(
            u,
            visibility=ProfileVisibility.public.value,
            searchable=True,
            mode=IdentityMode.pseudonymous.value,
        )
        r = SearchService.search("pseudouser")
        assert any(uu.username == "pseudouser" for uu in r.users)

    def test_inactive_user_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("inactiveuser")
        u.is_active = False
        _db.session.commit()
        r = SearchService.search("inactiveuser")
        assert r.users == []

    def test_shadow_banned_user_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("shadowbanned")
        u.is_shadow_banned = True
        _db.session.commit()
        r = SearchService.search("shadowbanned")
        assert r.users == []

    def test_draft_posts_still_excluded(self, db_session):  # noqa: ARG002
        """People search does not accidentally expose draft posts."""
        author = make_public_user("draftauthor")
        PostService.create(author.id, "Secret Draft Title", "invisible")
        r = SearchService.search("Secret Draft Title")
        assert r.post_total == 0


# ── Suggest — people group ─────────────────────────────────────────────────────


class TestSuggestUsers:
    def test_suggest_returns_users_key(self, db_session):  # noqa: ARG002
        result = SearchService.suggest("hello")
        assert "users" in result
        assert isinstance(result["users"], list)

    def test_suggest_public_user_returned(self, db_session):  # noqa: ARG002
        make_public_user("suggestme", display_name="Suggest Me")
        result = SearchService.suggest("suggestme")
        usernames = [u["username"] for u in result["users"]]
        assert "suggestme" in usernames

    def test_suggest_private_user_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("privatesugg")
        add_privacy(u, visibility=ProfileVisibility.private.value)
        result = SearchService.suggest("privatesugg")
        usernames = [u["username"] for u in result["users"]]
        assert "privatesugg" not in usernames

    def test_suggest_anonymous_excluded(self, db_session):  # noqa: ARG002
        u = make_public_user("anonsugg")
        add_privacy(
            u,
            visibility=ProfileVisibility.public.value,
            mode=IdentityMode.anonymous.value,
        )
        result = SearchService.suggest("anonsugg")
        usernames = [u["username"] for u in result["users"]]
        assert "anonsugg" not in usernames

    def test_suggest_user_entry_has_required_keys(self, db_session):  # noqa: ARG002
        make_public_user("keycheck")
        result = SearchService.suggest("keycheck")
        assert result["users"]
        entry = result["users"][0]
        assert "username" in entry
        assert "display_name" in entry
        assert "avatar_url" in entry

    def test_suggest_short_query_returns_empty(self, db_session):  # noqa: ARG002
        make_public_user("shortq")
        result = SearchService.suggest("s")
        assert result["users"] == []
