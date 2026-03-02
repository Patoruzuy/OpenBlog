"""PromptMetadata model.

A ``Post`` row with ``kind='prompt'`` may have exactly one ``PromptMetadata``
row that stores prompt-library-specific fields.

Design notes
------------
- ``post_id`` is the PK — one-to-one with the parent Post.
- ``variables_json`` is stored as TEXT (JSON string) for SQLite compatibility
  in tests; PostgreSQL callers should treat it as freeform JSON.
- ``complexity_level`` is free-form text but should be one of:
    beginner | intermediate | advanced
  (validated at the service layer).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.extensions import db

_COMPLEXITY_VALUES: frozenset[str] = frozenset({"beginner", "intermediate", "advanced"})


class PromptMetadata(db.Model):
    """Extra metadata for prompt posts (kind='prompt')."""

    __tablename__ = "prompt_metadata"

    # One-to-one with Post; post_id is also the PK.
    post_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posts.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    # ── Classification ────────────────────────────────────────────────────
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    intended_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    complexity_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="intermediate", server_default="intermediate"
    )

    # ── Content fields ────────────────────────────────────────────────────
    # Stored as a JSON string (TEXT) for SQLite compatibility.
    # Shape: {"VAR_NAME": "description", ...}
    variables_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    usage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    example_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    example_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationship ──────────────────────────────────────────────────────
    post: Mapped[object] = relationship(
        "Post",
        foreign_keys=[post_id],
        back_populates="prompt_metadata",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<PromptMetadata post_id={self.post_id} category={self.category!r}"
            f" complexity={self.complexity_level!r}>"
        )
