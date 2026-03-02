"""Prompt service — create and manage prompt-library posts.

Design
------
Prompts are ``Post`` rows with ``kind='prompt'``.  All core versioning,
revision, and AI-review machinery already operates on Post PKs and therefore
works for prompts transparently.

Scope isolation
---------------
Every query that returns prompts scopes on BOTH ``Post.kind == 'prompt'`` AND
the expected ``Post.workspace_id`` value (NULL for public, workspace.id for
workspace-scoped).  This prevents cross-kind and cross-workspace leakage.

Public prompts (workspace_id IS NULL):
- Listed on /prompts
- Included in /feed.xml, /feed.json, /sitemap.xml when published

Workspace prompts (workspace_id IS NOT NULL):
- Listed on /w/<slug>/prompts
- NEVER appear in public feeds or sitemap

Revision + versioning
---------------------
Use the existing ``revision_service`` + ``post_version_service`` directly
on the prompt's post_id — no extra code needed here.

AI review
---------
Use the existing ``ai_review_service`` on the prompt's post_id.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.prompt_metadata import PromptMetadata
from backend.models.user import User
from backend.utils.markdown import reading_time_minutes

_COMPLEXITY_VALUES: frozenset[str] = frozenset({"beginner", "intermediate", "advanced"})
_RESERVED_SLUGS: frozenset[str] = frozenset({"new", "edit", "draft", "drafts", "preview"})


# ── Exception ─────────────────────────────────────────────────────────────────


class PromptError(Exception):
    """Domain-level error for prompt operations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Slug helpers ──────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "prompt"


def _unique_prompt_slug(workspace_id: int | None, base: str) -> str:
    """Return a slug unique within (workspace layer, kind='prompt')."""
    candidate = base[:80]
    counter = 0
    while True:
        slug = candidate if counter == 0 else f"{candidate}-{counter}"
        if slug in _RESERVED_SLUGS:
            counter += 1
            continue
        exists = db.session.scalar(
            select(Post.id).where(
                Post.workspace_id == workspace_id if workspace_id is not None
                else Post.workspace_id.is_(None),
                Post.kind == "prompt",
                Post.slug == slug,
            )
        )
        if exists is None:
            return slug
        counter += 1


# ── Metadata validation ───────────────────────────────────────────────────────


def _validate_complexity(value: str) -> str:
    value = value.strip().lower()
    if value not in _COMPLEXITY_VALUES:
        raise PromptError(
            f"complexity_level must be one of: {', '.join(sorted(_COMPLEXITY_VALUES))}"
        )
    return value


def _normalise_variables(variables: dict | str | None) -> str:
    """Serialise variables to a JSON string for storage."""
    if variables is None:
        return "{}"
    if isinstance(variables, str):
        try:
            parsed = json.loads(variables)
        except json.JSONDecodeError as exc:
            raise PromptError(f"variables_json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise PromptError("variables_json must be a JSON object")
        return json.dumps(parsed)
    if isinstance(variables, dict):
        return json.dumps(variables)
    raise PromptError("variables must be a dict or JSON string")


# ── Public API ────────────────────────────────────────────────────────────────


def create_prompt(
    *,
    title: str,
    markdown_body: str,
    author: User,
    workspace_id: int | None = None,
    category: str,
    intended_model: str | None = None,
    variables: dict | str | None = None,
    usage_notes: str | None = None,
    example_input: str | None = None,
    example_output: str | None = None,
    complexity_level: str = "intermediate",
    status: PostStatus = PostStatus.draft,
    seo_description: str | None = None,
) -> Post:
    """Create a new prompt post + its metadata row.  Caller must commit.

    Returns the new ``Post`` instance (kind='prompt').
    """
    title = title.strip()
    if not title:
        raise PromptError("Title is required")
    category = category.strip()
    if not category:
        raise PromptError("Category is required")
    complexity_level = _validate_complexity(complexity_level)
    variables_json = _normalise_variables(variables)

    base_slug = _slugify(title)
    slug = _unique_prompt_slug(workspace_id, base_slug)
    now = datetime.now(UTC)

    published_at: datetime | None = None
    if status == PostStatus.published:
        published_at = now

    post = Post(
        title=title,
        slug=slug,
        markdown_body=markdown_body.strip(),
        kind="prompt",
        status=status,
        author_id=author.id,
        workspace_id=workspace_id,
        reading_time_minutes=reading_time_minutes(markdown_body),
        seo_description=seo_description,
        created_at=now,
        updated_at=now,
        published_at=published_at,
    )
    db.session.add(post)
    db.session.flush()  # assign post.id before creating metadata FK

    meta = PromptMetadata(
        post_id=post.id,
        category=category,
        intended_model=intended_model,
        complexity_level=complexity_level,
        variables_json=variables_json,
        usage_notes=usage_notes,
        example_input=example_input,
        example_output=example_output,
    )
    db.session.add(meta)
    return post


def get_prompt(post_id: int, *, workspace_id: int | None = None) -> Post | None:
    """Return a prompt Post by its PK, scoped to the given workspace layer.

    Returns ``None`` if no matching prompt is found (wrong kind, wrong
    workspace, or not found).
    """
    post: Post | None = db.session.scalar(
        select(Post)
        .where(
            Post.id == post_id,
            Post.kind == "prompt",
            Post.workspace_id == workspace_id
            if workspace_id is not None
            else Post.workspace_id.is_(None),
        )
        .options(joinedload(Post.author), joinedload(Post.tags))
    )
    return post


def get_prompt_by_slug(
    slug: str,
    *,
    workspace_id: int | None = None,
) -> Post | None:
    """Return a prompt Post by slug, scoped to the correct workspace layer.

    Returns ``None`` for misses and cross-scope requests.
    """
    return db.session.scalar(
        select(Post)
        .where(
            Post.slug == slug,
            Post.kind == "prompt",
            Post.workspace_id == workspace_id
            if workspace_id is not None
            else Post.workspace_id.is_(None),
        )
        .options(joinedload(Post.author), joinedload(Post.tags))
    )


def list_prompts(
    *,
    workspace_id: int | None = None,
    status: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Post]:
    """Return a list of prompts, scoped to the workspace layer.

    Parameters
    ----------
    workspace_id:
        ``None`` → public layer.  An integer → workspace layer.
    status:
        Optional PostStatus string filter.
    category:
        Optional category string filter (exact match, case-insensitive).
    limit / offset:
        Pagination.  ``limit`` is capped at 200.
    """
    limit = min(limit, 200)

    stmt = (
        select(Post)
        .where(
            Post.kind == "prompt",
            Post.workspace_id == workspace_id
            if workspace_id is not None
            else Post.workspace_id.is_(None),
        )
        .options(joinedload(Post.author), joinedload(Post.tags))
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit)
        .offset(offset)
    )

    if status is not None:
        try:
            st = PostStatus(status)
        except ValueError as exc:
            raise PromptError(f"Unknown status: {status!r}") from exc
        stmt = stmt.where(Post.status == st)

    if category is not None:
        # SQLAlchemy lower() for case-insensitive match works on both pg and sqlite
        from sqlalchemy import func  # noqa: PLC0415

        stmt = stmt.join(PromptMetadata, PromptMetadata.post_id == Post.id).where(
            func.lower(PromptMetadata.category) == category.strip().lower()
        )
    else:
        stmt = stmt.outerjoin(PromptMetadata, PromptMetadata.post_id == Post.id)

    return list(db.session.scalars(stmt).unique())


def update_prompt_metadata(
    post_id: int,
    *,
    category: str | None = None,
    intended_model: str | None = None,
    variables: dict | str | None = None,
    usage_notes: str | None = None,
    example_input: str | None = None,
    example_output: str | None = None,
    complexity_level: str | None = None,
) -> PromptMetadata:
    """Update metadata fields for an existing prompt.  Caller must commit.

    Raises ``PromptError`` (404) if no ``PromptMetadata`` row exists for
    *post_id*.
    """
    meta: PromptMetadata | None = db.session.get(PromptMetadata, post_id)
    if meta is None:
        raise PromptError(f"No prompt metadata for post_id={post_id}", status_code=404)

    if category is not None:
        category = category.strip()
        if not category:
            raise PromptError("Category cannot be empty")
        meta.category = category

    if intended_model is not None:
        meta.intended_model = intended_model.strip() or None

    if variables is not None:
        meta.variables_json = _normalise_variables(variables)

    if usage_notes is not None:
        meta.usage_notes = usage_notes or None

    if example_input is not None:
        meta.example_input = example_input or None

    if example_output is not None:
        meta.example_output = example_output or None

    if complexity_level is not None:
        meta.complexity_level = _validate_complexity(complexity_level)

    return meta


def get_prompt_metadata(post_id: int) -> PromptMetadata | None:
    """Return the PromptMetadata row for *post_id*, or None."""
    return db.session.get(PromptMetadata, post_id)


def parsed_variables(meta: PromptMetadata) -> dict:
    """Return the variables_json field parsed to a dict (empty dict on failure)."""
    try:
        result = json.loads(meta.variables_json or "{}")
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
