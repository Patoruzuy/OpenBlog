"""Tests for Ontology-Aware Fork Recommendation Filtering.

Coverage
--------
  ORF-001  recommend() with ontology_node returns only forks mapped to that node.
  ORF-002  recommend() with ontology_node=None returns all forks (unchanged).
  ORF-003  Descendant-mapped fork is included in the node slice.
  ORF-004  Non-mapped fork is excluded from the node slice.
  ORF-005  Workspace-only mapped fork excluded when using public scope.
  ORF-006  GET /ontology/<slug>/recommendations returns 200.
  ORF-007  GET /w/<ws>/ontology/<slug>/recommendations returns 200 for member.
"""

from __future__ import annotations

import itertools

from backend.extensions import db as _db
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.workspace import WorkspaceMember, WorkspaceMemberRole
from backend.services import fork_recommendation_service as svc
from backend.services import workspace_service as ws_svc
from backend.services.content_ontology_service import set_mappings
from backend.services.ontology_service import create_node

_ctr = itertools.count(26_000)


def _n() -> int:
    return next(_ctr)


def _make_user(role: str = "reader"):
    from backend.models.user import UserRole
    from backend.services.auth_service import AuthService

    n = _n()
    user = AuthService.register(f"orf{n}@example.com", f"orfuser{n}", "StrongPass123!!")
    if role != "reader":
        user.role = UserRole(role)
        _db.session.commit()
    return user


def _make_workspace(owner):
    n = _n()
    ws = ws_svc.create_workspace(name=f"ORF WS {n}", owner=owner)
    _db.session.commit()
    return ws


def _add_member(ws, user, role=WorkspaceMemberRole.viewer):
    _db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    _db.session.commit()


def _make_prompt(author, *, workspace_id=None, status=PostStatus.published):
    n = _n()
    p = Post(
        title=f"ORF-Prompt {n}",
        slug=f"orf-prompt-{n}",
        kind="prompt",
        markdown_body="body",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        version=1,
    )
    _db.session.add(p)
    _db.session.flush()
    return p


def _make_fork(base: Post, author, *, workspace_id=None, version: int = 1) -> Post:
    n = _n()
    fork = Post(
        title=f"ORF-Fork {n}",
        slug=f"orf-fork-{n}",
        kind="prompt",
        markdown_body="body",
        status=PostStatus.published,
        author_id=author.id,
        workspace_id=workspace_id,
        version=version,
    )
    _db.session.add(fork)
    _db.session.flush()
    _db.session.add(
        ContentLink(
            from_post_id=fork.id,
            to_post_id=base.id,
            link_type="derived_from",
            created_by_user_id=author.id,
        )
    )
    _db.session.flush()
    return fork


def _make_node(admin, *, parent_id=None):
    n = _n()
    node = create_node(
        admin,
        f"orf-node-{n}",
        f"ORF Node {n}",
        is_public=True,
        parent_id=parent_id,
    )
    _db.session.commit()
    return node


def _map(user, prompt, node, *, workspace=None):
    set_mappings(user, prompt, [node.id], workspace=workspace)
    _db.session.commit()


def _login(client, user):
    with client.session_transaction() as sess:
        sess["user_id"] = user.id


# ── ORF-001 ───────────────────────────────────────────────────────────────────


class TestOntologyNodeFilterReturnsMappedForks:
    def test_only_mapped_fork_returned(self, db_session):
        """ORF-001"""
        admin = _make_user("admin")
        user = _make_user()
        node = _make_node(admin)
        base = _make_prompt(user)
        mapped_fork = _make_fork(base, user)
        unmapped_fork = _make_fork(base, user)
        # Map only mapped_fork → node.
        _map(admin, mapped_fork, node)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None, ontology_node=node)
        rec_ids = {r.post_id for r in recs}
        assert mapped_fork.id in rec_ids, "Mapped fork should appear"
        assert unmapped_fork.id not in rec_ids, "Unmapped fork should be excluded"


# ── ORF-002 ───────────────────────────────────────────────────────────────────


class TestNoOntologyNodeReturnAllForks:
    def test_no_node_filter_returns_all_forks(self, db_session):
        """ORF-002"""
        user = _make_user()
        base = _make_prompt(user)
        fork_a = _make_fork(base, user)
        fork_b = _make_fork(base, user)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None, ontology_node=None)
        rec_ids = {r.post_id for r in recs}
        assert fork_a.id in rec_ids
        assert fork_b.id in rec_ids


# ── ORF-003 ───────────────────────────────────────────────────────────────────


class TestDescendantMappedForkIncluded:
    def test_fork_mapped_to_child_node_appears_for_parent(self, db_session):
        """ORF-003: fork mapped to child node appears when querying parent."""
        admin = _make_user("admin")
        user = _make_user()
        parent = _make_node(admin)
        child = _make_node(admin, parent_id=parent.id)
        base = _make_prompt(user)
        fork = _make_fork(base, user)
        # Map fork to child, query parent.
        _map(admin, fork, child)
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None, ontology_node=parent)
        assert any(r.post_id == fork.id for r in recs), (
            "Fork mapped to descendant node should appear for parent query"
        )


# ── ORF-004 ───────────────────────────────────────────────────────────────────


class TestNonMappedForkExcluded:
    def test_non_mapped_fork_excluded(self, db_session):
        """ORF-004"""
        admin = _make_user("admin")
        user = _make_user()
        node = _make_node(admin)
        base = _make_prompt(user)
        fork = _make_fork(base, user)
        # fork is NOT mapped to node.
        _db.session.commit()

        recs = svc.recommend(user, base, workspace=None, ontology_node=node)
        assert not any(r.post_id == fork.id for r in recs), "Non-mapped fork must be excluded"


# ── ORF-005 ───────────────────────────────────────────────────────────────────


class TestWsOnlyMappedForkExcludedPublic:
    def test_ws_only_mapping_excluded_from_public(self, db_session):
        """ORF-005"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        base = _make_prompt(owner)
        fork = _make_fork(base, owner)
        # Only workspace mapping — no public mapping.
        _map(owner, fork, node, workspace=ws)
        _db.session.commit()

        user = _make_user()
        recs = svc.recommend(user, base, workspace=None, ontology_node=node)
        assert not any(r.post_id == fork.id for r in recs), (
            "Fork with ws-only mapping must not appear in public recommendation slice"
        )


# ── ORF-006 ───────────────────────────────────────────────────────────────────


class TestPublicNodeRecommendationsRoute:
    def test_returns_200(self, db_session, auth_client):
        """ORF-006"""
        admin = _make_user("admin")
        user = _make_user()
        node = _make_node(admin)
        _login(auth_client, user)

        resp = auth_client.get(f"/ontology/{node.slug}/recommendations")
        assert resp.status_code == 200

    def test_missing_node_returns_404(self, db_session, auth_client):
        """ORF-006 – missing slug"""
        user = _make_user()
        _login(auth_client, user)

        resp = auth_client.get("/ontology/no-such-node-orf/recommendations")
        assert resp.status_code == 404


# ── ORF-007 ───────────────────────────────────────────────────────────────────


class TestWsNodeRecommendationsRoute:
    def test_member_gets_200(self, db_session, auth_client):
        """ORF-007"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        ws = _make_workspace(owner)
        node = _make_node(admin)
        _login(auth_client, owner)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/recommendations")
        assert resp.status_code == 200

    def test_non_member_gets_404(self, db_session, auth_client):
        """ORF-007 – non-member"""
        admin = _make_user("admin")
        owner = _make_user("editor")
        outsider = _make_user()
        ws = _make_workspace(owner)
        node = _make_node(admin)
        _login(auth_client, outsider)

        resp = auth_client.get(f"/w/{ws.slug}/ontology/{node.slug}/recommendations")
        assert resp.status_code == 404
