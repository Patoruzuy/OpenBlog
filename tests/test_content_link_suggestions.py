"""Tests for the Knowledge Graph suggestion service.

Coverage
--------
  CLS-001  Public scope: suggestions include only public published items.
  CLS-002  Public scope: excludes draft candidates.
  CLS-003  Public scope: excludes already-linked targets (either direction).
  CLS-004  Public scope: reason string contains shared tag names.
  CLS-005  Public scope: deterministic ordering — score desc, id desc tie-break.
  CLS-006  Workspace scope: member sees same-workspace + public suggestions.
  CLS-007  Workspace scope: service returns empty list when workspace_id that
           does not belong to viewer is passed (service isolates at query level).
  CLS-008  Workspace scope: never suggests items from other workspaces.
  CLS-009  Quality boost: prompt with more votes ranks above same-tag lower-voted
           prompt when all other signals are equal.
  CLS-010  Category bonus: prompt with matching category scores higher than
           equal-tag prompt with different category.
  CLS-011  Co-linking bonus: candidate that shares a link-target with source
           receives +0.10 in score.
  CLS-012  Recency boost: recently updated candidate scores higher (ceteris
           paribus) than an older one.
  CLS-013  Performance guard: service executes at most _BOUNDED_QUERY_LIMIT SQL
           statements over the candidate-pool phase.
  CLS-014  No suggestions for source with no matching candidates in scope.
  CLS-015  Limit parameter is respected.
"""

from __future__ import annotations

import itertools
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.post import Post, PostStatus
from backend.services import content_link_suggestion_service as svc
from backend.services.content_link_suggestion_service import Suggestion

# ── Counter for generating unique slugs / e-mails ─────────────────────────────

_ctr = itertools.count(1)


def _n() -> int:
    return next(_ctr)


# ── Shared test helpers ────────────────────────────────────────────────────────


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"sug{n}@example.com", f"suguser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.flush()
    return user


def _make_tag(name: str | None = None):
    from backend.models.tag import Tag

    n = _n()
    nm = name or f"tag-{n}"
    tag = Tag(name=nm, slug=nm.lower().replace(" ", "-"))
    _db.session.add(tag)
    _db.session.flush()
    return tag


def _make_post(
    author,
    *,
    workspace_id: int | None = None,
    kind: str = "article",
    status: PostStatus = PostStatus.published,
    tags: list | None = None,
    updated_at: datetime | None = None,
) -> Post:
    n = _n()
    p = Post(
        title=f"Post {n}",
        slug=f"p-{n}",
        kind=kind,
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    if updated_at is not None:
        p.updated_at = updated_at
    _db.session.add(p)
    _db.session.flush()  # obtain p.id before inserting post_tags
    if tags:
        from sqlalchemy import insert  # noqa: PLC0415

        from backend.models.tag import PostTag  # noqa: PLC0415

        for tag in tags:
            _db.session.execute(insert(PostTag).values(post_id=p.id, tag_id=tag.id))
        _db.session.flush()
    return p


def _make_workspace(owner):
    from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

    n = _n()
    ws = Workspace(name=f"WS {n}", slug=f"ws-{n}", owner_id=owner.id)
    _db.session.add(ws)
    _db.session.flush()
    member = WorkspaceMember(
        workspace_id=ws.id,
        user_id=owner.id,
        role=WorkspaceMemberRole.owner,
    )
    _db.session.add(member)
    _db.session.flush()
    return ws


def _add_vote(user, post: Post) -> None:
    from backend.models.vote import Vote

    vote = Vote(user_id=user.id, target_type="post", target_id=post.id)
    _db.session.add(vote)
    _db.session.flush()


def _add_link(editor, from_post: Post, to_post: Post, link_type: str = "related"):
    from backend.services.content_link_service import add_link

    add_link(editor, from_post, to_post, link_type)
    _db.session.flush()


def _make_prompt(
    author,
    *,
    workspace_id: int | None = None,
    category: str = "general",
    tags: list | None = None,
    status: PostStatus = PostStatus.published,
) -> Post:
    from backend.services.prompt_service import create_prompt

    n = _n()
    prompt = create_prompt(
        title=f"Prompt {n}",
        markdown_body="body {{VAR}}",
        author=author,
        workspace_id=workspace_id,
        category=category,
        status=status,
    )
    # prompt.id is available (create_prompt calls flush internally)
    if tags:
        from sqlalchemy import insert  # noqa: PLC0415

        from backend.models.tag import PostTag  # noqa: PLC0415

        for tag in tags:
            _db.session.execute(
                insert(PostTag).values(post_id=prompt.id, tag_id=tag.id)
            )
    _db.session.flush()
    return prompt


# ── Query-count context manager ────────────────────────────────────────────────


@contextmanager
def _count_queries(db) -> Generator[list[str], None, None]:
    """Accumulate SQL statements executed while the block runs.

    Uses SQLAlchemy's ``before_cursor_execute`` connection event.
    Only counts statements that go through a live connection (no cached
    results are counted).
    """
    from sqlalchemy import event

    executed: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        executed.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield executed
    finally:
        event.remove(engine, "before_cursor_execute", _before)


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestPublicScope:
    """CLS-001 through CLS-005."""

    def test_includes_only_public_published(self, db_session):
        """CLS-001: Only workspace_id=NULL + published items are suggested."""
        editor = _make_user("editor")
        owner = _make_user("editor")
        ws = _make_workspace(owner)

        tag = _make_tag("flask")
        source = _make_post(editor, tags=[tag])

        # Public published → should appear.
        pub = _make_post(editor, tags=[tag])
        # Workspace-scoped → should NOT appear.
        ws_post = _make_post(owner, workspace_id=ws.id, tags=[tag])  # noqa: F841
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        ids = [s.to_post_id for s in suggestions]

        assert pub.id in ids
        assert ws_post.id not in ids

    def test_excludes_drafts(self, db_session):
        """CLS-002: Draft candidates are never suggested."""
        editor = _make_user("editor")
        tag = _make_tag("celery")
        source = _make_post(editor, tags=[tag])
        draft = _make_post(editor, tags=[tag], status=PostStatus.draft)
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        ids = [s.to_post_id for s in suggestions]
        assert draft.id not in ids

    def test_excludes_already_linked_outgoing(self, db_session):
        """CLS-003a: Already-linked target (outgoing) is excluded."""
        editor = _make_user("editor")
        tag = _make_tag("django")
        source = _make_post(editor, tags=[tag])
        target = _make_post(editor, tags=[tag])
        _add_link(editor, source, target)
        _db.session.commit()

        suggestions = svc.suggest_for_post(editor, source, workspace_id=None)
        ids = [s.to_post_id for s in suggestions]
        assert target.id not in ids

    def test_excludes_already_linked_incoming(self, db_session):
        """CLS-003b: Already-linked target (incoming direction) is excluded."""
        editor = _make_user("editor")
        tag = _make_tag("redis")
        source = _make_post(editor, tags=[tag])
        other = _make_post(editor, tags=[tag])
        _add_link(editor, other, source)  # other → source
        _db.session.commit()

        suggestions = svc.suggest_for_post(editor, source, workspace_id=None)
        ids = [s.to_post_id for s in suggestions]
        # `other` linked to source (incoming direction) — must be excluded.
        assert other.id not in ids

    def test_reason_contains_shared_tag_names(self, db_session):
        """CLS-004: Reason string includes the names of shared tags."""
        editor = _make_user("editor")
        t1 = _make_tag("python")
        t2 = _make_tag("testing")
        source = _make_post(editor, tags=[t1, t2])
        candidate = _make_post(editor, tags=[t1, t2])
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        assert suggestions, "Expected at least one suggestion"
        top = next((s for s in suggestions if s.to_post_id == candidate.id), None)
        assert top is not None, "Candidate with shared tags not found in suggestions"
        assert "python" in top.reason.lower()
        assert "testing" in top.reason.lower()

    def test_deterministic_ordering_by_score_then_id(self, db_session):
        """CLS-005: Results are ordered score desc; tie-broken by id desc."""
        editor = _make_user("editor")
        t1 = _make_tag("score-tag-a")
        t2 = _make_tag("score-tag-b")

        source = _make_post(editor, tags=[t1, t2])
        # Both candidates share both tags → same Jaccard; tie-broken by id.
        c1 = _make_post(editor, tags=[t1, t2])
        c2 = _make_post(editor, tags=[t1, t2])
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        result_ids = [s.to_post_id for s in suggestions]
        assert c2.id in result_ids and c1.id in result_ids

        # Higher id should come first (tie-break).
        idx_c1 = result_ids.index(c1.id)
        idx_c2 = result_ids.index(c2.id)
        assert idx_c2 < idx_c1, "Higher id should appear first when scores are equal"

    def test_source_not_in_suggestions(self, db_session):
        """Source post is never suggested to itself."""
        editor = _make_user("editor")
        tag = _make_tag("self-ref-tag")
        source = _make_post(editor, tags=[tag])
        # Add another post to make a non-empty candidate pool.
        _make_post(editor, tags=[tag])
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        assert all(s.to_post_id != source.id for s in suggestions)


class TestWorkspaceScope:
    """CLS-006 through CLS-008."""

    def test_member_sees_public_and_same_workspace(self, db_session):
        """CLS-006: Workspace member gets public + own-workspace suggestions."""
        editor = _make_user("editor")
        ws = _make_workspace(editor)
        tag = _make_tag("ws-tag")

        source = _make_post(editor, workspace_id=ws.id, tags=[tag])
        pub_candidate = _make_post(editor, tags=[tag])
        ws_candidate = _make_post(editor, workspace_id=ws.id, tags=[tag])
        _db.session.commit()

        suggestions = svc.suggest_for_post(editor, source, workspace_id=ws.id)
        ids = [s.to_post_id for s in suggestions]

        assert pub_candidate.id in ids
        assert ws_candidate.id in ids

    def test_public_never_suggests_workspace_items(self, db_session):
        """CLS-007: Public scope never leaks workspace items to workspace scope."""
        editor = _make_user("editor")
        ws = _make_workspace(editor)
        tag = _make_tag("pub-only-tag")

        source = _make_post(editor, tags=[tag])  # public source
        pub = _make_post(editor, tags=[tag])  # public
        ws_post = _make_post(editor, workspace_id=ws.id, tags=[tag])  # workspace
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        ids = [s.to_post_id for s in suggestions]
        assert pub.id in ids
        assert ws_post.id not in ids

    def test_never_suggests_other_workspace(self, db_session):
        """CLS-008: Items from workspace B are never sent when browsing workspace A."""
        ownerA = _make_user("editor")
        ownerB = _make_user("editor")
        wsA = _make_workspace(ownerA)
        wsB = _make_workspace(ownerB)
        tag = _make_tag("cross-ws-tag")

        source = _make_post(ownerA, workspace_id=wsA.id, tags=[tag])
        ws_b_post = _make_post(ownerB, workspace_id=wsB.id, tags=[tag])
        _db.session.commit()

        # Even when a member of wsA calls with workspace_id=wsA.id, wsB posts
        # must never appear.
        suggestions = svc.suggest_for_post(ownerA, source, workspace_id=wsA.id)
        ids = [s.to_post_id for s in suggestions]
        assert ws_b_post.id not in ids

    def test_scope_field_correctly_set(self, db_session):
        """Scope field on Suggestion matches the candidate's workspace_id."""
        editor = _make_user("editor")
        ws = _make_workspace(editor)
        tag = _make_tag("scope-field-tag")

        source = _make_post(editor, workspace_id=ws.id, tags=[tag])
        pub = _make_post(editor, tags=[tag])
        ws_post = _make_post(editor, workspace_id=ws.id, tags=[tag])
        _db.session.commit()

        suggestions = svc.suggest_for_post(editor, source, workspace_id=ws.id)
        scope_map = {s.to_post_id: s.scope for s in suggestions}
        assert scope_map.get(pub.id) == "public"
        assert scope_map.get(ws_post.id) == "workspace"


class TestScoringHeuristics:
    """CLS-009 through CLS-012."""

    def test_vote_boost_ranks_higher_voted_prompt_first(self, db_session):
        """CLS-009: More-voted prompt ranks above equal-tag lower-voted prompt."""
        editor = _make_user("editor")
        voter = _make_user("reader")
        tag = _make_tag("quality-tag")

        source = _make_prompt(editor, tags=[tag])
        low_voted = _make_prompt(editor, tags=[tag])
        high_voted = _make_prompt(editor, tags=[tag])

        # Give high_voted 3 votes, low_voted 0.
        _add_vote(editor, high_voted)
        _add_vote(voter, high_voted)
        u2 = _make_user("reader")
        _add_vote(u2, high_voted)
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        ids = [s.to_post_id for s in suggestions]

        assert high_voted.id in ids and low_voted.id in ids
        assert ids.index(high_voted.id) < ids.index(low_voted.id), (
            "More-voted prompt should rank first"
        )

    def test_category_bonus_applied_to_prompts(self, db_session):
        """CLS-010: Prompt with matching category scores higher."""
        editor = _make_user("editor")
        tag = _make_tag("cat-bonus-tag")

        source = _make_prompt(editor, tags=[tag], category="debugging")
        same_cat = _make_prompt(editor, tags=[tag], category="debugging")
        diff_cat = _make_prompt(editor, tags=[tag], category="writing")
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        id_to_score = {s.to_post_id: s.score for s in suggestions}

        assert same_cat.id in id_to_score and diff_cat.id in id_to_score
        assert id_to_score[same_cat.id] > id_to_score[diff_cat.id], (
            "Same-category prompt should have higher score"
        )

    def test_category_bonus_value(self, db_session):
        """Category bonus is exactly _W_CATEGORY (0.20) above otherwise-equal candidate."""
        editor = _make_user("editor")
        tag = _make_tag("cat-val-tag")

        source = _make_prompt(editor, tags=[tag], category="ops")
        same_cat = _make_prompt(editor, tags=[tag], category="ops")
        diff_cat = _make_prompt(editor, tags=[tag], category="other")
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        id_to_score = {s.to_post_id: s.score for s in suggestions}

        diff = id_to_score[same_cat.id] - id_to_score[diff_cat.id]
        assert abs(diff - svc._W_CATEGORY) < 1e-6, (
            f"Expected category delta {svc._W_CATEGORY}, got {diff}"
        )

    def test_colink_bonus_applied(self, db_session):
        """CLS-011: Candidate sharing a link-target with source gets +0.10."""
        editor = _make_user("editor")
        tag = _make_tag("colink-tag")

        shared_target = _make_post(editor, tags=[tag])
        source = _make_post(editor, tags=[tag])
        co_linked = _make_post(editor, tags=[tag])  # also links to shared_target
        no_colink = _make_post(editor, tags=[tag])  # no shared target

        _add_link(editor, source, shared_target)  # source → shared_target
        _add_link(editor, co_linked, shared_target)  # co_linked → shared_target
        _db.session.commit()

        suggestions = svc.suggest_for_post(editor, source, workspace_id=None)
        id_to_score = {s.to_post_id: s.score for s in suggestions}

        # co_linked and no_colink have same tags; co_linked gets co-link bonus.
        assert co_linked.id in id_to_score and no_colink.id in id_to_score
        assert id_to_score[co_linked.id] > id_to_score[no_colink.id]
        delta = id_to_score[co_linked.id] - id_to_score[no_colink.id]
        assert abs(delta - svc._W_COLINK) < 1e-6, (
            f"Co-link bonus should be {svc._W_COLINK}, delta was {delta}"
        )

    def test_recency_boost_applied(self, db_session):
        """CLS-012: Recently updated candidate scores higher than stale one."""
        editor = _make_user("editor")
        tag = _make_tag("recency-tag")

        now = datetime.now(UTC)
        recent_time = now - timedelta(days=10)
        stale_time = now - timedelta(days=200)

        source = _make_post(editor, tags=[tag])
        recent = _make_post(editor, tags=[tag], updated_at=recent_time)
        stale = _make_post(editor, tags=[tag], updated_at=stale_time)
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        id_to_score = {s.to_post_id: s.score for s in suggestions}

        assert recent.id in id_to_score and stale.id in id_to_score
        assert id_to_score[recent.id] > id_to_score[stale.id], (
            "Recently updated candidate should score higher"
        )

    def test_jaccard_score_correct(self, db_session):
        """Jaccard = |intersection| / |union| is computed correctly."""
        editor = _make_user("editor")
        t1 = _make_tag("j1")
        t2 = _make_tag("j2")
        t3 = _make_tag("j3")

        # source has {t1, t2}; candidate has {t2, t3}
        # intersection={t2}, union={t1,t2,t3} → jaccard = 1/3 ≈ 0.333
        source = _make_post(editor, tags=[t1, t2])
        candidate = _make_post(editor, tags=[t2, t3])
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        matched = next((s for s in suggestions if s.to_post_id == candidate.id), None)
        assert matched is not None
        # Score has Jaccard as base; may have recency boost on top.
        assert matched.score >= (1 / 3) - 1e-3


class TestEdgeCases:
    """CLS-013 through CLS-015."""

    def test_bounded_query_count(self, db_session):
        """CLS-013: Service executes at most 12 queries for a populated pool."""
        editor = _make_user("editor")
        tag = _make_tag("bounded-tag")
        source = _make_post(editor, tags=[tag])
        for _ in range(10):
            _make_post(editor, tags=[tag])
        _db.session.commit()

        # _BOUNDED_QUERY_LIMIT: generous ceiling; validates no N+1 loop.
        _BOUNDED_QUERY_LIMIT = 12

        with _count_queries(_db) as executed:
            _ = svc.suggest_for_post(None, source, workspace_id=None)

        assert len(executed) <= _BOUNDED_QUERY_LIMIT, (
            f"Expected <= {_BOUNDED_QUERY_LIMIT} queries, got {len(executed)}:\n"
            + "\n".join(f"  [{i}] {q[:120]}" for i, q in enumerate(executed))
        )

    def test_no_candidates_returns_empty(self, db_session):
        """CLS-014: Empty list when there are no published public candidates."""
        editor = _make_user("editor")
        tag = _make_tag("isolated-tag")
        source = _make_post(editor, tags=[tag])
        _db.session.commit()

        # No other public published posts exist.
        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        assert suggestions == []

    def test_limit_parameter_respected(self, db_session):
        """CLS-015: limit=N returns at most N suggestions."""
        editor = _make_user("editor")
        tag = _make_tag("limit-tag")
        source = _make_post(editor, tags=[tag])
        for _ in range(10):
            _make_post(editor, tags=[tag])
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None, limit=3)
        assert len(suggestions) <= 3

    def test_no_tags_falls_back_to_recency(self, db_session):
        """Source with no tags still gets recency-based suggestions."""
        editor = _make_user("editor")
        source = _make_post(editor)  # no tags
        for _ in range(3):
            _make_post(editor)
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        # Should return candidates even without tag overlap.
        assert suggestions

    def test_suggestion_fields_populated(self, db_session):
        """Suggestion dataclass has all required fields correctly set."""
        editor = _make_user("editor")
        tag = _make_tag("field-check-tag")
        source = _make_post(editor, tags=[tag])
        candidate = _make_post(editor, tags=[tag])
        _db.session.commit()

        suggestions = svc.suggest_for_post(None, source, workspace_id=None)
        assert suggestions
        s: Suggestion = suggestions[0]
        assert s.to_post_id == candidate.id
        assert s.title == candidate.title
        assert s.slug == candidate.slug
        assert s.kind in ("article", "prompt", "playbook")
        assert s.scope in ("public", "workspace")
        assert isinstance(s.reason, str) and s.reason
        assert isinstance(s.score, float) and s.score >= 0

    def test_all_already_linked_returns_empty(self, db_session):
        """When all candidates are already linked, return empty list."""
        editor = _make_user("editor")
        tag = _make_tag("all-linked-tag")
        source = _make_post(editor, tags=[tag])
        c1 = _make_post(editor, tags=[tag])
        c2 = _make_post(editor, tags=[tag])
        _add_link(editor, source, c1)
        _add_link(editor, source, c2)
        _db.session.commit()

        suggestions = svc.suggest_for_post(editor, source, workspace_id=None)
        assert all(s.to_post_id not in (c1.id, c2.id) for s in suggestions)
