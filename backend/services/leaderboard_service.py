"""Leaderboard service — deterministic ranked lists from reputation_totals.

Scope isolation
---------------
Every function enforces scope purely in SQL — templates and routes never
filter results.

Public leaderboard:
  ``reputation_totals.workspace_id IS NULL``
  Rank: ``points_total DESC, user_id DESC``

Workspace leaderboard:
  ``reputation_totals.workspace_id = ws.id``
  INNER JOIN workspace_members ensures only current members appear.
  Rank: ``points_total DESC, user_id DESC``

Ontology leaderboards:
  Contributors are authors of published posts mapped to the node or its
  descendants via content_ontology.  Scope filters differ:

  Public:
    - Only mappings where ``content_ontology.workspace_id IS NULL``
    - Only posts where ``posts.workspace_id IS NULL`` AND status='published'
    - Ranked by public reputation_totals (workspace_id IS NULL)

  Workspace:
    - Mappings: ``co.workspace_id IS NULL OR co.workspace_id = ws.id``
    - Posts: ``(post.workspace_id IS NULL OR post.workspace_id = ws.id)`` AND published
    - Ranked by workspace reputation_totals (workspace_id = ws.id)
    - INNER JOIN workspace_members enforces member-only ranking

Query budget
------------
All functions execute ≤ 2 SQL statements:
  1. ``ontology_service.get_all_descendant_ids`` (BFS, 1 query)  — ontology routes only
  2. Main ranked SELECT with subquery inlined

This keeps every route within the ≤ 8 query budget.
"""

from __future__ import annotations

import dataclasses

from sqlalchemy import or_, select

from backend.extensions import db
from backend.models.ontology import ContentOntology, OntologyNode
from backend.models.post import Post, PostStatus
from backend.models.reputation_total import ReputationTotal
from backend.models.user import User
from backend.models.workspace import Workspace, WorkspaceMember
from backend.services import ontology_service


@dataclasses.dataclass(slots=True)
class LeaderboardRow:
    """A single ranked entry in a leaderboard."""

    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    points_total: int
    rank: int


class LeaderboardService:
    """Static-method service for all leaderboard queries."""

    # ── Public leaderboard ────────────────────────────────────────────────────

    @staticmethod
    def get_public_leaderboard(limit: int = 50) -> list[LeaderboardRow]:
        """Rank all users by public reputation (workspace_id IS NULL).

        Tie-break: ``user_id DESC`` (deterministic, stable across calls).
        """
        rows = db.session.execute(
            select(
                ReputationTotal.user_id,
                User.username,
                User.display_name,
                User.avatar_url,
                ReputationTotal.points_total,
            )
            .join(User, User.id == ReputationTotal.user_id)
            .where(ReputationTotal.workspace_id.is_(None))
            .order_by(
                ReputationTotal.points_total.desc(),
                ReputationTotal.user_id.desc(),
            )
            .limit(limit)
        ).all()

        return [
            LeaderboardRow(
                user_id=r.user_id,
                username=r.username,
                display_name=r.display_name,
                avatar_url=r.avatar_url,
                points_total=r.points_total,
                rank=i + 1,
            )
            for i, r in enumerate(rows)
        ]

    # ── Workspace leaderboard ─────────────────────────────────────────────────

    @staticmethod
    def get_workspace_leaderboard(
        workspace: Workspace, limit: int = 50
    ) -> list[LeaderboardRow]:
        """Rank workspace members by their workspace reputation total.

        The INNER JOIN on ``workspace_members`` ensures that users who have a
        stale ``reputation_totals`` row but are no longer members are excluded.
        Route layer is responsible for gating non-member access; this join is
        defense-in-depth only.
        """
        rows = db.session.execute(
            select(
                ReputationTotal.user_id,
                User.username,
                User.display_name,
                User.avatar_url,
                ReputationTotal.points_total,
            )
            .join(User, User.id == ReputationTotal.user_id)
            .join(
                WorkspaceMember,
                (WorkspaceMember.user_id == ReputationTotal.user_id)
                & (WorkspaceMember.workspace_id == workspace.id),
            )
            .where(ReputationTotal.workspace_id == workspace.id)
            .order_by(
                ReputationTotal.points_total.desc(),
                ReputationTotal.user_id.desc(),
            )
            .limit(limit)
        ).all()

        return [
            LeaderboardRow(
                user_id=r.user_id,
                username=r.username,
                display_name=r.display_name,
                avatar_url=r.avatar_url,
                points_total=r.points_total,
                rank=i + 1,
            )
            for i, r in enumerate(rows)
        ]

    # ── Public ontology leaderboard ───────────────────────────────────────────

    @staticmethod
    def get_public_ontology_leaderboard(
        node: OntologyNode, limit: int = 50
    ) -> list[LeaderboardRow]:
        """Rank contributors by public reputation within an ontology node.

        Only considers:
          - ``content_ontology.workspace_id IS NULL``  (public mappings only)
          - ``posts.workspace_id IS NULL``              (public posts only)
          - ``posts.status = 'published'``

        Ranked by ``reputation_totals WHERE workspace_id IS NULL``.
        Descendants of *node* are included via a BFS traversal (1 SQL query).
        """
        node_ids = ontology_service.get_all_descendant_ids(node.id, public_only=True)
        if not node_ids:
            return []

        # Subquery: distinct author_ids of eligible posts
        contributor_stmt = (
            select(Post.author_id)
            .distinct()
            .join(ContentOntology, ContentOntology.post_id == Post.id)
            .where(
                ContentOntology.ontology_node_id.in_(node_ids),
                ContentOntology.workspace_id.is_(None),
                Post.workspace_id.is_(None),
                Post.status == PostStatus.published,
            )
        )

        rows = db.session.execute(
            select(
                ReputationTotal.user_id,
                User.username,
                User.display_name,
                User.avatar_url,
                ReputationTotal.points_total,
            )
            .join(User, User.id == ReputationTotal.user_id)
            .where(
                ReputationTotal.workspace_id.is_(None),
                ReputationTotal.user_id.in_(contributor_stmt),
            )
            .order_by(
                ReputationTotal.points_total.desc(),
                ReputationTotal.user_id.desc(),
            )
            .limit(limit)
        ).all()

        return [
            LeaderboardRow(
                user_id=r.user_id,
                username=r.username,
                display_name=r.display_name,
                avatar_url=r.avatar_url,
                points_total=r.points_total,
                rank=i + 1,
            )
            for i, r in enumerate(rows)
        ]

    # ── Workspace ontology leaderboard ────────────────────────────────────────

    @staticmethod
    def get_workspace_ontology_leaderboard(
        workspace: Workspace,
        node: OntologyNode,
        limit: int = 50,
    ) -> list[LeaderboardRow]:
        """Rank workspace members by workspace reputation within an ontology node.

        Eligible mappings:
          ``content_ontology.workspace_id IS NULL OR = ws.id``

        Eligible posts:
          ``posts.workspace_id IS NULL OR = ws.id`` AND published

        Ranked by ``reputation_totals WHERE workspace_id = ws.id``.
        INNER JOIN on workspace_members restricts results to current members.
        """
        # public_only=False: workspace may reference private nodes in overlays
        node_ids = ontology_service.get_all_descendant_ids(node.id, public_only=False)
        if not node_ids:
            return []

        contributor_stmt = (
            select(Post.author_id)
            .distinct()
            .join(ContentOntology, ContentOntology.post_id == Post.id)
            .where(
                ContentOntology.ontology_node_id.in_(node_ids),
                or_(
                    ContentOntology.workspace_id.is_(None),
                    ContentOntology.workspace_id == workspace.id,
                ),
                or_(
                    Post.workspace_id.is_(None),
                    Post.workspace_id == workspace.id,
                ),
                Post.status == PostStatus.published,
            )
        )

        rows = db.session.execute(
            select(
                ReputationTotal.user_id,
                User.username,
                User.display_name,
                User.avatar_url,
                ReputationTotal.points_total,
            )
            .join(User, User.id == ReputationTotal.user_id)
            .join(
                WorkspaceMember,
                (WorkspaceMember.user_id == ReputationTotal.user_id)
                & (WorkspaceMember.workspace_id == workspace.id),
            )
            .where(
                ReputationTotal.workspace_id == workspace.id,
                ReputationTotal.user_id.in_(contributor_stmt),
            )
            .order_by(
                ReputationTotal.points_total.desc(),
                ReputationTotal.user_id.desc(),
            )
            .limit(limit)
        ).all()

        return [
            LeaderboardRow(
                user_id=r.user_id,
                username=r.username,
                display_name=r.display_name,
                avatar_url=r.avatar_url,
                points_total=r.points_total,
                rank=i + 1,
            )
            for i, r in enumerate(rows)
        ]
