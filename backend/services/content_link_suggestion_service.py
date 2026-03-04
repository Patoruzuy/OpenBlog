"""Content-link suggestion service — deterministic heuristic recommendations.

Scoring model
-------------
For each candidate post / prompt visible under the given scope:

    score = jaccard_tag_overlap               # base; 0.0 when no shared tags
          + 0.20  (same prompt category — both kind='prompt')
          + 0.10  (co-linking: candidate shares at least one link-target with source)
          + 0.10 * norm_votes  (quality normalised by max votes in candidate pool)
          + 0.05  (recency boost: updated within last 90 days)

Maximum possible score: 1.45 (perfect Jaccard + all bonuses).

Tie-break: higher ``to_post_id`` wins (stable for deterministic tests).

Scope isolation
---------------
- Public context  (workspace_id=None) → published, workspace_id IS NULL only.
- Workspace context (workspace_id=ws_id) → published and
    (workspace_id IS NULL  OR  workspace_id = ws_id).
  Items from a *different* workspace are never returned.

A non-member calling suggest_for_post with workspace_id=ws_id they cannot
access is prevented at the route layer (the route aborts 404 before calling
the service).  This service enforces scope at the query level regardless.

Exclusions
----------
- The source post itself.
- Posts already linked from source in *any* direction and *any* link_type.

Query pattern (bounded, no N+1)
--------------------------------
1.  Source tag IDs          — 1 query (PostTag WHERE post_id=source_id)
2.  Candidate posts         — 1 query (JOIN PostTag WHERE tag_id IN source_tags,
                              OR recency fallback when source has no tags)
3.  Optional supplement     — 1 query when tag pool < _MIN_TAGGED_POOL
4.  Existing links          — 1 query (ContentLink WHERE from OR to = source)
5.  Candidate tag ID pairs  — 1 query (PostTag WHERE post_id IN candidate_ids)
6.  All tag names           — 1 query (Tag WHERE id IN all_tag_ids)
7.  Vote counts             — 1 aggregation query
8.  Prompt metadata         — 1 query (PromptMetadata WHERE post_id IN candidate_ids)
9.  Co-link expansions      — 1 query (ContentLink WHERE to_post_id IN source_targets)

Total: 7–9 bounded SQL queries regardless of candidate set size.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from backend.extensions import db
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.prompt_metadata import PromptMetadata
from backend.models.tag import PostTag, Tag
from backend.models.vote import Vote

# ── Constants ──────────────────────────────────────────────────────────────────

_CANDIDATE_CAP: int = 200
_MIN_TAGGED_POOL: int = 10
_RECENCY_WINDOW: timedelta = timedelta(days=90)

# Scoring weights
_W_CATEGORY: float = 0.20
_W_COLINK: float = 0.10
_W_QUALITY: float = 0.10  # max vote-based boost
_W_RECENCY: float = 0.05


# ── Public interface ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Suggestion:
    """A single suggested content-link target."""

    to_post_id: int
    title: str
    slug: str
    kind: str
    scope: str  # 'public' | 'workspace'
    reason: str  # Human-readable explanation shown in UI
    score: float


def suggest_for_post(
    viewer: object,
    post: Post,
    workspace_id: int | None = None,
    limit: int = 8,
) -> list[Suggestion]:
    """Return up to *limit* suggested link targets for *post*.

    Parameters
    ----------
    viewer:
        The ``User`` object for the current request (may be ``None`` for
        anonymous visitors).  Not used for filtering (scope is controlled by
        *workspace_id*) but reserved for future personalisation.
    post:
        Source post / prompt whose detail page is being rendered.
    workspace_id:
        ``None`` → public scope; integer → workspace scope.
        **Workspace membership must be verified by the caller** (route layer)
        before passing a non-None value here.
    limit:
        Maximum suggestions returned (default 8, cap at ``_CANDIDATE_CAP``).
    """
    actual_limit = min(limit, _CANDIDATE_CAP)
    source_id = post.id

    # ── 1. Source tag IDs ─────────────────────────────────────────────────
    # Access via ORM relationship (lazy-loads if not yet cached) so we use
    # the same session as everything else and avoid any raw-table visibility
    # window issues with newly-committed rows.
    source_tag_ids: set[int] = {tag.id for tag in post.tags}

    # ── 2 + 3. Candidate posts ────────────────────────────────────────────
    base_filters = [
        Post.id != source_id,
        Post.status == PostStatus.published,
    ]
    if workspace_id is None:
        base_filters.append(Post.workspace_id.is_(None))
    else:
        base_filters.append(
            or_(Post.workspace_id.is_(None), Post.workspace_id == workspace_id)
        )

    candidates: list[Post]

    if source_tag_ids:
        # Candidates that share at least one tag with source.
        tagged_post_ids_sq = (
            select(PostTag.c.post_id)
            .where(PostTag.c.tag_id.in_(source_tag_ids))
            .where(PostTag.c.post_id != source_id)
            .distinct()
            .limit(_CANDIDATE_CAP)
            .subquery()
        )
        tag_match_stmt = (
            select(Post)
            .where(*base_filters)
            .where(Post.id.in_(select(tagged_post_ids_sq.c.post_id)))
        )
        candidates = list(db.session.execute(tag_match_stmt).scalars().all())

        # Supplement with recency fallback when tag pool too small.
        if len(candidates) < _MIN_TAGGED_POOL:
            existing_ids = {p.id for p in candidates}
            recency_stmt = (
                select(Post)
                .where(*base_filters)
                .where(Post.id.not_in(existing_ids | {source_id}))
                .order_by(Post.updated_at.desc())
                .limit(_CANDIDATE_CAP - len(candidates))
            )
            candidates += list(db.session.execute(recency_stmt).scalars().all())
    else:
        # Source has no tags — use recency pool.
        recency_stmt = (
            select(Post)
            .where(*base_filters)
            .order_by(Post.updated_at.desc())
            .limit(_CANDIDATE_CAP)
        )
        candidates = list(db.session.execute(recency_stmt).scalars().all())

    if not candidates:
        return []

    candidate_ids: list[int] = [p.id for p in candidates]

    # ── 4. Existing links for exclusion + co-link base ────────────────────
    existing_link_rows = db.session.execute(
        select(ContentLink.from_post_id, ContentLink.to_post_id).where(
            or_(
                ContentLink.from_post_id == source_id,
                ContentLink.to_post_id == source_id,
            )
        )
    ).all()

    already_linked: set[int] = set()
    source_outgoing_targets: set[int] = set()
    for row in existing_link_rows:
        already_linked.add(row.from_post_id)
        already_linked.add(row.to_post_id)
        if row.from_post_id == source_id:
            source_outgoing_targets.add(row.to_post_id)
    already_linked.discard(source_id)

    # Filter candidates to those not already linked.
    candidates = [p for p in candidates if p.id not in already_linked]
    if not candidates:
        return []

    candidate_ids = [p.id for p in candidates]

    # ── 5. Candidate tag ID pairs ─────────────────────────────────────────
    tag_pairs = db.session.execute(
        select(PostTag.c.post_id, PostTag.c.tag_id).where(
            PostTag.c.post_id.in_(candidate_ids)
        )
    ).all()
    candidate_tags: dict[int, set[int]] = {cid: set() for cid in candidate_ids}
    all_involved_tag_ids: set[int] = set(source_tag_ids)
    for post_id, tag_id in tag_pairs:
        candidate_tags[post_id].add(tag_id)
        all_involved_tag_ids.add(tag_id)

    # ── 6. Tag names (for reason text) ────────────────────────────────────
    tag_names: dict[int, str] = {}
    if all_involved_tag_ids:
        tag_name_rows = db.session.execute(
            select(Tag.id, Tag.name).where(Tag.id.in_(all_involved_tag_ids))
        ).all()
        tag_names = {row.id: row.name for row in tag_name_rows}

    # ── 7. Vote counts (quality boost) ────────────────────────────────────
    vote_rows = db.session.execute(
        select(Vote.target_id, func.count().label("cnt"))
        .where(Vote.target_type == "post")
        .where(Vote.target_id.in_(candidate_ids))
        .group_by(Vote.target_id)
    ).all()
    vote_counts: dict[int, int] = {row.target_id: row.cnt for row in vote_rows}
    max_votes: int = max(vote_counts.values(), default=0)

    # ── 8. Prompt metadata (category comparison) ──────────────────────────
    prompt_meta_rows = db.session.execute(
        select(PromptMetadata.post_id, PromptMetadata.category).where(
            PromptMetadata.post_id.in_(candidate_ids)
        )
    ).all()
    prompt_categories: dict[int, str] = {
        row.post_id: row.category for row in prompt_meta_rows
    }

    # Source post category (for prompt category comparison).
    source_category: str | None = None
    if post.kind == "prompt" and post.prompt_metadata:
        source_category = post.prompt_metadata.category

    # ── 9. Co-link expansion ──────────────────────────────────────────────
    co_linked_ids: set[int] = set()
    if source_outgoing_targets:
        co_link_rows = (
            db.session.execute(
                select(ContentLink.from_post_id).where(
                    ContentLink.to_post_id.in_(source_outgoing_targets),
                    ContentLink.from_post_id != source_id,
                )
            )
            .scalars()
            .all()
        )
        co_linked_ids = set(co_link_rows)

    # ── Score each candidate ─────────────────────────────────────────────
    now_utc = datetime.now(UTC)
    recency_cutoff = now_utc - _RECENCY_WINDOW
    suggestions: list[Suggestion] = []

    for candidate in candidates:
        cid = candidate.id
        c_tags = candidate_tags.get(cid, set())

        # Jaccard tag overlap.
        if source_tag_ids or c_tags:
            intersection = source_tag_ids & c_tags
            union = source_tag_ids | c_tags
            jaccard = len(intersection) / len(union) if union else 0.0
        else:
            intersection = set()
            jaccard = 0.0

        score = jaccard

        # Category bonus (prompts only).
        category_match = False
        if (
            source_category
            and candidate.kind == "prompt"
            and prompt_categories.get(cid) == source_category
        ):
            score += _W_CATEGORY
            category_match = True

        # Co-linking bonus.
        if cid in co_linked_ids:
            score += _W_COLINK

        # Quality / vote boost (normalised, avoids divide-by-zero).
        if max_votes > 0:
            score += _W_QUALITY * (vote_counts.get(cid, 0) / max_votes)

        # Recency boost.
        # updated_at may be timezone-aware or naive depending on DB backend;
        # treat naive datetimes as UTC for SQLite test compatibility.
        updated = candidate.updated_at
        if updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if updated >= recency_cutoff:
                score += _W_RECENCY

        # Build reason string.
        reason = _build_reason(
            shared_tag_ids=intersection,
            tag_names=tag_names,
            category_match=category_match,
            source_category=source_category,
            co_linked=(cid in co_linked_ids),
        )

        scope = "public" if candidate.workspace_id is None else "workspace"
        suggestions.append(
            Suggestion(
                to_post_id=cid,
                title=candidate.title,
                slug=candidate.slug,
                kind=candidate.kind,
                scope=scope,
                reason=reason,
                score=round(score, 4),
            )
        )

    # Sort: score descending; tie-break by id descending (deterministic).
    suggestions.sort(key=lambda s: (s.score, s.to_post_id), reverse=True)
    return suggestions[:actual_limit]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_reason(
    shared_tag_ids: set[int],
    tag_names: dict[int, str],
    category_match: bool,
    source_category: str | None,
    co_linked: bool,
) -> str:
    """Return a short human-readable explanation of why this was suggested."""
    parts: list[str] = []

    if shared_tag_ids:
        names = sorted(tag_names.get(tid, str(tid)) for tid in shared_tag_ids)
        parts.append(f"Shared tags: {', '.join(names)}")

    if category_match and source_category:
        parts.append(f"Same prompt category: {source_category}")

    if co_linked:
        parts.append("Co-linked: shares associations")

    return "; ".join(parts) if parts else "Similar content"
