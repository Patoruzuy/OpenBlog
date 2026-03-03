"""Tests for the public Intelligence Dashboard route.

Coverage
--------
  IP-001  GET /intelligence returns 200 with no auth.
  IP-002  Empty DB: all four sections show "No data found."
  IP-003  A scored public prompt appears in the top-prompts section.
  IP-004  A fork prompt renders the "fork" badge.
  IP-005  An ontology-mapped prompt appears in the ontology section.
  IP-006  Public route does NOT set Cache-Control: private.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from backend.extensions import db as _db
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.content_link import ContentLink
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus

_ctr = itertools.count(1000)


def _n() -> int:
    return next(_ctr)


def _make_user():
    from backend.models.user import User, UserRole  # noqa: PLC0415

    n = _n()
    u = User(
        email=f"ip{n}@example.com",
        username=f"ipuser{n}",
        password_hash="x",
        role=UserRole("reader"),
    )
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_prompt(author, *, workspace_id=None):
    n = _n()
    p = Post(
        title=f"IP-Prompt {n}",
        slug=f"ip-prompt-{n}",
        kind="prompt",
        markdown_body="hello",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_benchmark_data(post, score: float, dt=None, workspace_id=None):
    """Create completed BenchmarkSuite + Case + Run + Result for *post*."""
    if dt is None:
        dt = datetime.now(UTC) - timedelta(days=5)
    n = _n()
    suite = BenchmarkSuite(
        name=f"IP-Suite {n}",
        slug=f"ip-suite-{n}",
        workspace_id=workspace_id,
        created_by_user_id=post.author_id,
        created_at=datetime.now(UTC),
    )
    _db.session.add(suite)
    _db.session.flush()

    case_ = BenchmarkCase(
        suite_id=suite.id,
        name=f"IP-Case {n}",
        input_json={},
        created_at=datetime.now(UTC),
    )
    _db.session.add(case_)
    _db.session.flush()

    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=post.id,
        prompt_version=1,
        workspace_id=workspace_id,
        model_name="test-model",
        status=BenchmarkRunStatus.completed.value,
        created_by_user_id=post.author_id,
        created_at=dt,
        completed_at=dt,
    )
    _db.session.add(run)
    _db.session.flush()

    result = BenchmarkRunResult(
        run_id=run.id,
        case_id=case_.id,
        output_text="ok",
        score_numeric=score,
        created_at=dt,
    )
    _db.session.add(result)
    _db.session.flush()
    return run, result


# ── IP-001 ─────────────────────────────────────────────────────────────────────


class TestPublicRoute200:
    def test_get_intelligence_no_auth_200(self, client, db_session):
        resp = client.get("/intelligence")
        assert resp.status_code == 200


# ── IP-002 ─────────────────────────────────────────────────────────────────────


class TestEmptyState:
    def test_all_sections_show_no_data_when_empty(self, client, db_session):
        resp = client.get("/intelligence")
        text = resp.get_data(as_text=True)
        assert text.count("No data found.") >= 4


# ── IP-003 ─────────────────────────────────────────────────────────────────────


class TestScoredPromptAppearsInTopSection:
    def test_scored_public_prompt_visible(self, client, db_session):
        user = _make_user()
        prompt = _make_prompt(user)
        _make_benchmark_data(prompt, score=0.85)
        _db.session.commit()

        resp = client.get("/intelligence")
        assert resp.status_code == 200
        assert prompt.title.encode() in resp.data


# ── IP-004 ─────────────────────────────────────────────────────────────────────


class TestForkBadge:
    def test_fork_prompt_shows_fork_badge(self, client, db_session):
        user = _make_user()
        origin = _make_prompt(user)
        fork = _make_prompt(user)

        # Mark fork as derived_from origin
        link = ContentLink(
            from_post_id=fork.id,
            to_post_id=origin.id,
            link_type="derived_from",
            created_by_user_id=user.id,
        )
        _db.session.add(link)
        _db.session.flush()

        _make_benchmark_data(fork, score=0.90)
        _db.session.commit()

        resp = client.get("/intelligence")
        assert resp.status_code == 200
        assert b"fork" in resp.data


# ── IP-005 ─────────────────────────────────────────────────────────────────────


class TestOntologySectionPopulated:
    def test_ontology_category_appears_in_section(self, client, db_session):
        user = _make_user()
        prompt = _make_prompt(user)

        node = OntologyNode(
            slug=f"ip-node-{_n()}",
            name="IP Category",
            created_by_user_id=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        _db.session.add(node)
        _db.session.flush()

        mapping = ContentOntology(
            post_id=prompt.id,
            ontology_node_id=node.id,
            workspace_id=None,
            created_by_user_id=user.id,
            created_at=datetime.now(UTC),
        )
        _db.session.add(mapping)
        _db.session.flush()

        _make_benchmark_data(prompt, score=0.75)
        _db.session.commit()

        resp = client.get("/intelligence")
        assert resp.status_code == 200
        assert b"IP Category" in resp.data


# ── IP-006 ─────────────────────────────────────────────────────────────────────


class TestPublicCacheHeader:
    def test_no_private_cache_control_on_public_route(self, client, db_session):
        resp = client.get("/intelligence")
        cc = resp.headers.get("Cache-Control", "")
        assert "private" not in cc
