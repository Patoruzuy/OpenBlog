"""Ontology models — concept tree and content mappings.

Design
------
OntologyNode     — a concept in the global ontology tree (parent/child).
ContentOntology  — maps a Post to an OntologyNode with workspace scope.

Scope model
-----------
``OntologyNode.is_public = True``
    The node is part of the public taxonomy and visible to all.
    (Nodes are global — no workspace_id on OntologyNode.)

``ContentOntology.workspace_id IS NULL``
    Public mapping.  Visible to all authenticated users.

``ContentOntology.workspace_id = ws.id``
    Workspace overlay mapping.  Visible only to members of that workspace.
    Items from other workspaces are NEVER returned.

Public pages:
    - Only public nodes (is_public=True).
    - Only public mappings (workspace_id IS NULL).
    - Only published, public prompts (posts.kind='prompt', workspace_id IS NULL, status='published').

Workspace pages:
    - Public nodes + overlay mappings for workspace.
    - Public prompts + workspace prompts from the SAME workspace.
    - Cross-workspace leakage prevented at the service layer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db


class OntologyNode(db.Model):
    """A concept in the system-wide ontology tree."""

    __tablename__ = "ontology_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ontology_nodes.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    parent: Mapped[OntologyNode | None] = relationship(
        "OntologyNode",
        remote_side="OntologyNode.id",
        foreign_keys=[parent_id],
        back_populates="children",
    )
    children: Mapped[list[OntologyNode]] = relationship(
        "OntologyNode",
        foreign_keys=[parent_id],
        back_populates="parent",
        order_by="OntologyNode.sort_order, OntologyNode.name",
    )
    created_by: Mapped[object] = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        Index("ix_ontology_nodes_parent_id_sort", "parent_id", "sort_order"),
        CheckConstraint("id != parent_id", name="ck_ontology_nodes_no_self_parent"),
    )

    def __repr__(self) -> str:
        return f"<OntologyNode id={self.id} slug={self.slug!r}>"


class ContentOntology(db.Model):
    """Maps a Post to an OntologyNode, optionally scoped to a workspace."""

    __tablename__ = "content_ontology"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ontology_node_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ontology_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL → public mapping; NOT NULL → workspace overlay.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped[object] = relationship("Post", foreign_keys=[post_id])
    node: Mapped[OntologyNode] = relationship(
        "OntologyNode", foreign_keys=[ontology_node_id]
    )
    created_by: Mapped[object] = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        # In SQLite and PostgreSQL: this covers (post, node, ws) tuples where
        # workspace_id is NOT NULL.  The service layer explicitly deduplicates
        # NULL workspace_id rows via delete-before-insert.
        UniqueConstraint(
            "post_id",
            "ontology_node_id",
            "workspace_id",
            name="uq_content_ontology_post_node_ws",
        ),
        Index("ix_content_ontology_post_ws", "post_id", "workspace_id"),
        Index("ix_content_ontology_node_ws", "ontology_node_id", "workspace_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<ContentOntology post_id={self.post_id} "
            f"node_id={self.ontology_node_id} ws={self.workspace_id!r}>"
        )
