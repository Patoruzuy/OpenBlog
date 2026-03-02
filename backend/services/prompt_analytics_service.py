"""Prompt Evolution Analytics service — deterministic, scope-isolated aggregations.

Design
------
Four public functions aggregate the four analytics panels shown on the
``/prompts/<slug>/analytics`` page:

- ``get_version_timeline``  — ordered list of accepted revisions with cumulative
                              vote counts and AI-attribution flags.
- ``get_rating_trend``      — per-version vote counts + delta from previous version.
- ``get_fork_tree``         — posts that list this prompt as ``derived_from`` in the
                              ContentLink graph, scope-filtered.
- ``get_execution_stats``   — total views, recent views, and unique readers.

Scope isolation
---------------
- ``workspace_id=None``  → public context.
  Fork tree queries include only published posts in the public layer
  (Post.workspace_id IS NULL).
- ``workspace_id=ws_id`` → workspace context.
  Fork tree queries include published posts that are public OR belong to ws_id.
  Items from a *different* workspace are excluded at the SQL level.

Votes, version history, and execution stats are scoped to the prompt post
itself and therefore carry no cross-workspace risk.

Query pattern (bounded, no N+1)
---------------------------------
get_version_timeline / get_rating_trend
  1  PostVersion  WHERE post_id=X ORDER BY version_number
  2  Revision     WHERE id IN (revision_ids from step 1)
  3  User         WHERE id IN (author_ids collected from revisions)
  4  PostReleaseNote  WHERE post_id=X ORDER BY version_number
  5  Vote timestamps  SELECT created_at WHERE target_type='post' AND target_id=X

get_fork_tree
  6  ContentLink  +JOIN Post  WHERE to_post_id=X AND link_type='derived_from'
                              + scope WHERE clause
  7  Vote counts  SELECT target_id, COUNT(*) … GROUP BY target_id

get_execution_stats
  8  AnalyticsEvent COUNT  WHERE post_id=X AND event_type='post_view' AND occurred_at > now-30d
  9  UserPostRead   COUNT  WHERE post_id=X

Total: 7–9 bounded queries regardless of data volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from backend.extensions import db
from backend.models.analytics import AnalyticsEvent
from backend.models.content_link import ContentLink
from backend.models.post import Post, PostStatus
from backend.models.post_release_note import PostReleaseNote
from backend.models.post_version import PostVersion
from backend.models.revision import Revision
from backend.models.user import User
from backend.models.user_post_read import UserPostRead
from backend.models.vote import Vote

# ── Result dataclasses ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VersionEntry:
    """A single entry in the prompt version timeline."""

    version_number: int
    created_at: datetime
    summary: str                 # release-note text or revision summary
    author_display: str          # display_name or username of revision author
    vote_count_cumulative: int   # total votes that existed when this version was created
    is_ai_generated: bool        # True when revision.source_metadata_json records AI origin


@dataclass(frozen=True)
class RatingSnapshot:
    """Vote count and delta for one version."""

    version_number: int
    vote_count: int   # cumulative votes at this version
    delta: int        # votes gained since the previous version (0 for v1)


@dataclass(frozen=True)
class ForkEntry:
    """A post that derives from the current prompt via a derived_from ContentLink."""

    post_id: int
    title: str
    slug: str
    kind: str
    scope: str        # 'public' | 'workspace'
    vote_count: int
    created_at: datetime


@dataclass(frozen=True)
class ExecutionStats:
    """Aggregate view / reader metrics for a prompt post."""

    total_views: int          # Post.view_count (denormalised counter)
    views_last_30_days: int   # AnalyticsEvent rows in the last 30 days
    unique_readers: int       # UserPostRead rows (one per authenticated user)


# ── Public interface ──────────────────────────────────────────────────────────


def get_version_timeline(
    prompt_post: Post,
    workspace_id: int | None = None,  # noqa: ARG001  (reserved for future scope use)
) -> list[VersionEntry]:
    """Return ordered version history for *prompt_post*.

    Entries are ordered by ``version_number`` ascending (oldest first).
    Includes the initial v1 implicit creation (represented by the first
    PostVersion row, if present).

    Votes are cumulated per-version by comparing vote timestamps to the
    PostVersion creation timestamp.
    """
    # ── 1. PostVersion records ────────────────────────────────────────────────
    versions: list[PostVersion] = (
        db.session.execute(
            select(PostVersion)
            .where(PostVersion.post_id == prompt_post.id)
            .order_by(PostVersion.version_number)
        )
        .scalars()
        .all()
    )

    if not versions:
        # No explicit version history yet — post still at v1 with no accepted revisions.
        return []

    # ── 2. Revisions for each version (via PostVersion.revision_id) ───────────
    revision_ids: list[int] = [
        v.revision_id for v in versions if v.revision_id is not None
    ]
    revision_map: dict[int, Revision] = {}
    if revision_ids:
        rows = db.session.execute(
            select(Revision).where(Revision.id.in_(revision_ids))
        ).scalars()
        revision_map = {r.id: r for r in rows}

    # ── 3. Authors ────────────────────────────────────────────────────────────
    author_ids: set[int] = set()
    for rev in revision_map.values():
        author_ids.add(rev.author_id)
    author_ids.add(prompt_post.author_id)  # v1 author fallback

    user_map: dict[int, User] = {}
    if author_ids:
        rows = db.session.execute(
            select(User).where(User.id.in_(author_ids))
        ).scalars()
        user_map = {u.id: u for u in rows}

    # ── 4. Release notes (preferred human-readable summary) ───────────────────
    release_notes: list[PostReleaseNote] = (
        db.session.execute(
            select(PostReleaseNote)
            .where(PostReleaseNote.post_id == prompt_post.id)
            .order_by(PostReleaseNote.version_number)
        )
        .scalars()
        .all()
    )
    release_note_map: dict[int, str] = {
        rn.version_number: rn.summary for rn in release_notes
    }

    # ── 5. Vote timestamps (all votes for this post, ordered ascending) ───────
    vote_times: list[datetime] = list(
        db.session.execute(
            select(Vote.created_at)
            .where(Vote.target_type == "post", Vote.target_id == prompt_post.id)
            .order_by(Vote.created_at)
        ).scalars()
    )

    # ── Assemble VersionEntry list ─────────────────────────────────────────────
    result: list[VersionEntry] = []
    for pv in versions:
        # Cumulative votes as of version creation time
        cum_votes = sum(1 for vt in vote_times if vt <= pv.created_at)

        # Summary: prefer release note, then revision summary, then fallback
        summary = release_note_map.get(pv.version_number, "")
        if not summary and pv.revision_id and pv.revision_id in revision_map:
            summary = revision_map[pv.revision_id].summary
        if not summary:
            summary = "Initial version" if pv.version_number == 1 else "Updated"

        # Author: from revision if available, else post author for v1
        author_id_for_version = (
            revision_map[pv.revision_id].author_id
            if (pv.revision_id and pv.revision_id in revision_map)
            else prompt_post.author_id
        )
        user = user_map.get(author_id_for_version)
        author_display = (
            (user.display_name or user.username) if user else "Unknown"
        )

        # AI attribution
        is_ai = False
        if pv.revision_id and pv.revision_id in revision_map:
            meta = revision_map[pv.revision_id].source_metadata_json
            is_ai = isinstance(meta, dict) and meta.get("source") == "ai_suggestion"

        result.append(
            VersionEntry(
                version_number=pv.version_number,
                created_at=pv.created_at,
                summary=summary,
                author_display=author_display,
                vote_count_cumulative=cum_votes,
                is_ai_generated=is_ai,
            )
        )

    return result


def get_rating_trend(
    prompt_post: Post,
    workspace_id: int | None = None,
) -> list[RatingSnapshot]:
    """Return per-version vote counts and deltas.

    Reuses the data gathered by ``get_version_timeline`` but presents it as
    a flat list of (version_number, vote_count, delta) triples suitable for
    rendering as a table or sparkline.

    Ordered ascending by version_number.
    """
    timeline = get_version_timeline(prompt_post, workspace_id=workspace_id)
    if not timeline:
        return []

    snapshots: list[RatingSnapshot] = []
    for i, entry in enumerate(timeline):
        prev_count = timeline[i - 1].vote_count_cumulative if i > 0 else 0
        delta = 0 if i == 0 else entry.vote_count_cumulative - prev_count
        snapshots.append(
            RatingSnapshot(
                version_number=entry.version_number,
                vote_count=entry.vote_count_cumulative,
                delta=delta,
            )
        )
    return snapshots


def get_fork_tree(
    prompt_post: Post,
    workspace_id: int | None = None,
) -> list[ForkEntry]:
    """Return posts that link to *prompt_post* with link_type='derived_from'.

    Scope isolation:
    - Public (workspace_id=None): only forks that are publicly published
      (Post.workspace_id IS NULL, status=published).
    - Workspace (workspace_id=ws_id): publicly published forks plus forks in
      the same workspace.  Forks from other workspaces are excluded.

    Results are ordered by ``created_at`` descending (newest forks first).
    """
    # ── 6. Content links pointing to this post with derived_from ─────────────
    scope_clause = (
        Post.workspace_id.is_(None)
        if workspace_id is None
        else or_(Post.workspace_id.is_(None), Post.workspace_id == workspace_id)
    )

    fork_rows = db.session.execute(
        select(Post)
        .join(ContentLink, ContentLink.from_post_id == Post.id)
        .where(
            ContentLink.to_post_id == prompt_post.id,
            ContentLink.link_type == "derived_from",
            Post.status == PostStatus.published,
            scope_clause,
        )
        .order_by(Post.created_at.desc())
    ).scalars().all()

    if not fork_rows:
        return []

    fork_ids = [p.id for p in fork_rows]

    # ── 7. Vote counts for forks ──────────────────────────────────────────────
    vote_rows = db.session.execute(
        select(Vote.target_id, func.count(Vote.id).label("cnt"))
        .where(Vote.target_type == "post", Vote.target_id.in_(fork_ids))
        .group_by(Vote.target_id)
    ).all()
    vote_map: dict[int, int] = {row.target_id: row.cnt for row in vote_rows}

    return [
        ForkEntry(
            post_id=p.id,
            title=p.title,
            slug=p.slug,
            kind=p.kind,
            scope="workspace" if p.workspace_id is not None else "public",
            vote_count=vote_map.get(p.id, 0),
            created_at=p.created_at,
        )
        for p in fork_rows
    ]


def get_execution_stats(
    prompt_post: Post,
    workspace_id: int | None = None,  # noqa: ARG001
) -> ExecutionStats:
    """Return view and reader metrics for *prompt_post*.

    Uses:
    - ``Post.view_count`` for the denormalised total.
    - ``AnalyticsEvent`` (event_type='post_view') for recent-30-day count.
    - ``UserPostRead`` for unique authenticated-reader count.

    These metrics are scoped to the post itself and carry no cross-workspace
    risk (no workspace column on Vote or AnalyticsEvent).
    """
    # ── 8. Recent views from AnalyticsEvent ──────────────────────────────────
    cutoff = datetime.now(UTC) - timedelta(days=30)
    views_recent: int = (
        db.session.execute(
            select(func.count(AnalyticsEvent.id)).where(
                AnalyticsEvent.post_id == prompt_post.id,
                AnalyticsEvent.event_type == "post_view",
                AnalyticsEvent.occurred_at >= cutoff,
            )
        ).scalar()
        or 0
    )

    # ── 9. Unique authenticated readers ───────────────────────────────────────
    unique_readers: int = (
        db.session.execute(
            select(func.count(UserPostRead.id)).where(
                UserPostRead.post_id == prompt_post.id
            )
        ).scalar()
        or 0
    )

    return ExecutionStats(
        total_views=prompt_post.view_count,
        views_last_30_days=views_recent,
        unique_readers=unique_readers,
    )
