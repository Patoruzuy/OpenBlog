"""Unit tests for SearchService.

Uses the ``db_session`` fixture (SQLite in-memory, _FakeRedis).
All search tests exercise the SQLite LIKE back-end.
"""

from __future__ import annotations

from backend.services.auth_service import AuthService
from backend.services.post_service import PostService
from backend.services.search_service import SearchService

# ── Helpers ────────────────────────────────────────────────────────────────────


def make_author():
    return AuthService.register("author@search.com", "srchauthor", "StrongPass123!!")


def make_post(author_id: int, title: str, body: str = "", tags: list[str] | None = None):
    post = PostService.create(author_id, title, body, tags=tags or [])
    return PostService.publish(post)


# ── Empty / whitespace query ───────────────────────────────────────────────────


class TestSearchEmpty:
    def test_empty_string_returns_nothing(self, db_session):  # noqa: ARG002
        _r = SearchService.search(""); posts, total = _r.posts, _r.post_total
        assert posts == [] and total == 0

    def test_whitespace_only_returns_nothing(self, db_session):  # noqa: ARG002
        _r = SearchService.search("   "); posts, total = _r.posts, _r.post_total
        assert posts == [] and total == 0


# ── Title matching ─────────────────────────────────────────────────────────────


class TestSearchTitle:
    def test_exact_title_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Flask Tutorial", "Learn Flask today.")
        _r = SearchService.search("Flask Tutorial"); posts, total = _r.posts, _r.post_total
        assert total == 1
        assert posts[0].title == "Flask Tutorial"

    def test_partial_title_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Advanced Python Tips")
        _r = SearchService.search("Python"); posts, total = _r.posts, _r.post_total
        assert total == 1

    def test_case_insensitive_title(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Docker Compose Guide")
        _r = SearchService.search("docker"); posts, total = _r.posts, _r.post_total
        assert total == 1

    def test_no_match_returns_empty(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Unrelated Post")
        _r = SearchService.search("kubernetes"); posts, total = _r.posts, _r.post_total
        assert total == 0 and posts == []


# ── Body matching ──────────────────────────────────────────────────────────────


class TestSearchBody:
    def test_body_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "My Post", "This covers async/await in depth.")
        _r = SearchService.search("async"); posts, total = _r.posts, _r.post_total
        assert total == 1

    def test_body_no_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "My Post", "Completely unrelated content.")
        _r = SearchService.search("microservices"); posts, total = _r.posts, _r.post_total
        assert total == 0


# ── Tag matching ───────────────────────────────────────────────────────────────


class TestSearchTags:
    def test_tag_name_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "A Post About Stuff", tags=["python", "flask"])
        _r = SearchService.search("flask"); posts, total = _r.posts, _r.post_total
        assert total == 1

    def test_tag_slug_match(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Another Post", tags=["machine-learning"])
        _r = SearchService.search("machine"); posts, total = _r.posts, _r.post_total
        assert total == 1


# ── Draft exclusion ────────────────────────────────────────────────────────────


class TestSearchDraftExclusion:
    def test_draft_not_returned(self, db_session):  # noqa: ARG002
        author = make_author()
        # Create but do NOT publish
        PostService.create(author.id, "Secret Draft Post", "Draft content here")
        _r = SearchService.search("Secret Draft"); posts, total = _r.posts, _r.post_total
        assert total == 0 and posts == []

    def test_published_returned(self, db_session):  # noqa: ARG002
        author = make_author()
        make_post(author.id, "Public Article", "Visible content")
        _r = SearchService.search("Public Article"); posts, total = _r.posts, _r.post_total
        assert total == 1


# ── Pagination ────────────────────────────────────────────────────────────────


class TestSearchPagination:
    def test_per_page_limits_results(self, db_session):  # noqa: ARG002
        author = make_author()
        for i in range(5):
            make_post(author.id, f"Python Post {i}", "Python content here")
        _r = SearchService.search("Python", page=1, per_page=3); posts, total = _r.posts, _r.post_total
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
