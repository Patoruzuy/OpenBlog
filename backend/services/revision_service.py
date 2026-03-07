"""Revision service — contributor edit proposals for published posts.

Workflow
--------
1. A contributor (any authenticated user who is *not* the post's author) calls
   ``submit()`` to propose a change to a published post.
2. An editor or admin calls ``accept()`` or ``reject()`` to review it.
3. On acceptance:
     - ``post.markdown_body`` is updated to the proposed content.
     - ``post.version`` is bumped by 1.
     - An immutable ``PostVersion`` snapshot is written.
     - The contributor earns ``ACCEPT_REPUTATION`` (+5) reputation.
     - A ``revision_accepted`` notification is sent to the contributor.
4. On rejection, a ``revision_rejected`` notification is sent with an optional
   human-readable note.

Rules
-----
- Only published posts can receive revisions.
- Post authors cannot submit revisions on their own posts (they should use the
  edit endpoint directly).
- Multiple pending revisions for the same post are allowed; the first to be
  accepted wins and others remain pending (they may become stale).
- ``proposed_markdown`` must differ from the current post body.
- A non-empty ``summary`` (commit-message-style) is required.

Staleness
---------
A revision is *stale* when ``post.version > revision.base_version_number``,
meaning another revision was accepted after this one was submitted.  The UI
should warn reviewers but still allow acceptance (human judgement call).
"""

from __future__ import annotations

import difflib
from datetime import UTC, datetime

from sqlalchemy import func, select

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User, UserRole
from backend.services.badge_service import BadgeService
from backend.utils import metrics


class RevisionError(Exception):
    """Domain error raised by RevisionService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RevisionService:
    """Static-method service for the revision proposal workflow."""

    #: Reputation points awarded to a contributor when their revision is accepted.
    ACCEPT_REPUTATION: int = 5

    # ── Submit ────────────────────────────────────────────────────────────────

    @staticmethod
    def submit(
        post_id: int,
        author_id: int,
        proposed_markdown: str,
        summary: str,
    ) -> Revision:
        """Submit a revision proposal for a published post.

        Parameters
        ----------
        post_id:
            Primary key of the target post.
        author_id:
            User ID of the contributor submitting the proposal.
        proposed_markdown:
            Full proposed markdown body (not a patch).
        summary:
            Required one-line description of the change.

        Returns
        -------
        Revision
            The newly created (pending) revision.

        Raises
        ------
        RevisionError 404
            Post does not exist.
        RevisionError 400
            Post is not published, author is the post owner, proposed markdown
            is identical to the current body, or summary is blank.
        """
        post = db.session.get(Post, post_id)
        if post is None:
            raise RevisionError("Post not found.", 404)
        if post.status != PostStatus.published:
            raise RevisionError(
                "Revisions can only be submitted for published posts.", 400
            )
        if post.author_id == author_id:
            raise RevisionError(
                "Post authors cannot submit revisions on their own posts; "
                "use the edit endpoint instead.",
                400,
            )

        summary = summary.strip()
        if not summary:
            raise RevisionError("A summary (commit message) is required.", 400)

        if proposed_markdown.strip() == post.markdown_body.strip():
            raise RevisionError(
                "Proposed markdown is identical to the current post body.", 400
            )

        # Snapshot: find the latest accepted PostVersion for this post.
        latest_version: PostVersion | None = db.session.scalar(
            select(PostVersion)
            .where(PostVersion.post_id == post_id)
            .order_by(PostVersion.version_number.desc())
            .limit(1)
        )

        # Base content for the diff: use the versioned snapshot when available,
        # otherwise the current post body (no versions yet → initial v1 state).
        base_markdown = (
            latest_version.markdown_body
            if latest_version is not None
            else post.markdown_body
        )

        # Pre-compute and cache the unified diff at submission time so
        # reviewers always get a fast response even if the base version is
        # later deleted.
        diff_cache = RevisionService._compute_diff(base_markdown, proposed_markdown)

        revision = Revision(
            post_id=post_id,
            author_id=author_id,
            base_version_id=latest_version.id if latest_version is not None else None,
            base_version_number=post.version,
            proposed_markdown=proposed_markdown,
            summary=summary,
            diff_cache=diff_cache,
            status=RevisionStatus.pending,
        )
        db.session.add(revision)
        db.session.commit()
        metrics.revisions_submitted.inc()
        return revision

    # ── Diff ─────────────────────────────────────────────────────────────────

    @staticmethod
    def get_diff(revision_id: int) -> str:
        """Return the unified diff for a revision.

        Uses the cached diff if present; otherwise recomputes from the base
        ``PostVersion`` (or the current post body as a last resort).  The
        recomputed diff is persisted back to ``diff_cache``.

        Raises
        ------
        RevisionError 404  revision not found
        """
        revision = db.session.get(Revision, revision_id)
        if revision is None:
            raise RevisionError("Revision not found.", 404)

        if revision.diff_cache:
            return revision.diff_cache

        # Recompute from the base version snapshot.
        base_version = (
            db.session.get(PostVersion, revision.base_version_id)
            if revision.base_version_id is not None
            else None
        )
        if base_version is not None:
            base_markdown = base_version.markdown_body
        else:
            # Fall back to the current post body (best-effort; may drift).
            post = db.session.get(Post, revision.post_id)
            base_markdown = post.markdown_body if post else ""

        diff = RevisionService._compute_diff(base_markdown, revision.proposed_markdown)

        # Persist the re-derived cache so subsequent calls are fast.
        revision.diff_cache = diff
        db.session.commit()
        return diff

    # ── Accept ───────────────────────────────────────────────────────────────

    @staticmethod
    def accept(revision_id: int, reviewer_id: int) -> Revision:
        """Accept a pending revision.

        All side-effects are committed atomically:

        - ``revision.status`` → ``accepted``
        - ``post.markdown_body`` updated to the proposed content
        - ``post.version`` incremented
        - New immutable ``PostVersion`` snapshot created
        - Contributor earns ``ACCEPT_REPUTATION`` reputation points
        - ``revision_accepted`` notification sent to the contributor

        Raises
        ------
        RevisionError 404  revision not found
        RevisionError 400  revision is not pending
        """
        revision = db.session.get(Revision, revision_id)
        if revision is None:
            raise RevisionError("Revision not found.", 404)
        if revision.status != RevisionStatus.pending:
            raise RevisionError(f"Revision is already {revision.status.value}.", 400)

        post = db.session.get(Post, revision.post_id)
        if post is None:
            raise RevisionError("Associated post not found.", 404)

        # Apply the proposed content and bump the version counter.
        post.markdown_body = revision.proposed_markdown
        post.version += 1

        # Create an immutable snapshot of the accepted content.
        version_snapshot = PostVersion(
            post_id=post.id,
            version_number=post.version,
            markdown_body=revision.proposed_markdown,
            accepted_by_id=reviewer_id,
            revision_id=revision.id,
        )
        db.session.add(version_snapshot)
        db.session.flush()  # obtain version_snapshot.id before commit

        # Create a changelog entry for this new version.
        from backend.services.release_notes_service import create_release_note

        create_release_note(
            post_id=post.id,
            version_number=post.version,
            summary=revision.summary,
            accepted_revision_id=revision.id,
            auto_generated=False,
        )

        # Mark the revision as accepted.
        revision.status = RevisionStatus.accepted
        revision.reviewed_by_id = reviewer_id
        revision.reviewed_at = datetime.now(UTC)

        # Promote reader → contributor on first accepted revision.
        contributor = db.session.get(User, revision.author_id)
        if contributor is not None and contributor.role == UserRole.reader:
            contributor.role = UserRole.contributor

        # Reputation fan-out via the auditable ledger.
        # award_event is idempotent (fingerprint guard) and commits atomically
        # together with all pending session state above.
        from backend.services.reputation_service import (
            ReputationService,  # noqa: PLC0415
        )

        _pts = ReputationService.POINTS_REVISION_ACCEPTED + (
            ReputationService.POINTS_PUBLIC_BONUS if post.workspace_id is None else 0
        )
        ReputationService.award_event(
            user_id=revision.author_id,
            workspace_id=post.workspace_id,
            event_type="revision_accepted",
            source_type="revision",
            source_id=revision.id,
            points=_pts,
            fingerprint_parts={"revision_id": revision.id},
            metadata={"post_id": post.id, "reviewer_id": reviewer_id},
        )

        # Award first-contribution badge if this is their first accepted revision.
        # Wrapped in try/except so a badge failure never aborts the acceptance.
        try:
            BadgeService.award(revision.author_id, "first_accepted_revision")
        except Exception:  # noqa: BLE001
            pass

        # Re-evaluate all contribution thresholds for the author.
        try:
            BadgeService.check_contribution_badges(
                revision.author_id, workspace_id=post.workspace_id
            )
        except Exception:  # noqa: BLE001
            pass

        db.session.commit()

        # Fanout in-app notification via subscription system.
        from backend.services.notification_service import emit as _emit  # noqa: PLC0415

        _emit(
            "revision.accepted",
            reviewer_id,
            "revision",
            revision.id,
            {
                "post_id": post.id,
                "post_slug": post.slug,
                "post_title": post.title,
                "version": post.version,
                "revision_author_id": revision.author_id,
                "revision_id": revision.id,
            },
        )
        metrics.revisions_accepted.inc()
        return revision

    # ── Reject ───────────────────────────────────────────────────────────────

    @staticmethod
    def reject(revision_id: int, reviewer_id: int, note: str = "") -> Revision:
        """Reject a pending revision with an optional reviewer note.

        Raises
        ------
        RevisionError 404  revision not found
        RevisionError 400  revision is not pending
        """
        revision = db.session.get(Revision, revision_id)
        if revision is None:
            raise RevisionError("Revision not found.", 404)
        if revision.status != RevisionStatus.pending:
            raise RevisionError(f"Revision is already {revision.status.value}.", 400)

        post = db.session.get(Post, revision.post_id)
        post_title = post.title if post else "the post"
        post_slug = post.slug if post else ""

        revision.status = RevisionStatus.rejected
        revision.reviewed_by_id = reviewer_id
        revision.reviewed_at = datetime.now(UTC)
        revision.rejection_note = note.strip() or None

        # Reputation penalty via the auditable ledger.
        from backend.services.reputation_service import (
            ReputationService,  # noqa: PLC0415
        )

        ReputationService.award_event(
            user_id=revision.author_id,
            workspace_id=post.workspace_id if post is not None else None,
            event_type="revision_rejected",
            source_type="revision",
            source_id=revision.id,
            points=ReputationService.POINTS_REVISION_REJECTED,
            fingerprint_parts={"revision_id": revision.id},
            metadata={
                "reviewer_id": reviewer_id,
                "rejection_note": note.strip(),
            },
        )

        db.session.commit()

        # Fanout in-app notification via subscription system.
        from backend.services.notification_service import emit as _emit  # noqa: PLC0415

        _emit(
            "revision.rejected",
            reviewer_id,
            "revision",
            revision.id,
            {
                "post_id": post.id if post else None,
                "post_slug": post_slug,
                "post_title": post_title,
                "revision_author_id": revision.author_id,
                "revision_id": revision.id,
                "rejection_note": note.strip(),
            },
        )
        metrics.revisions_rejected.inc()
        return revision

    # ── Query helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def get_by_id(revision_id: int) -> Revision | None:
        """Return a ``Revision`` by primary key, or ``None``."""
        return db.session.get(Revision, revision_id)

    @staticmethod
    def list_for_post(
        post_id: int,
        page: int = 1,
        per_page: int = 20,
        *,
        status: RevisionStatus | None = None,
    ) -> tuple[list[Revision], int]:
        """Return paginated revisions for a post, newest first.

        Parameters
        ----------
        status:
            When provided, filter to only revisions with this status value.
        """
        q = select(Revision).where(Revision.post_id == post_id)
        if status is not None:
            q = q.where(Revision.status == status)
        q = q.order_by(Revision.created_at.desc())

        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        revisions = list(
            db.session.scalars(q.offset((page - 1) * per_page).limit(per_page))
        )
        return revisions, total

    @staticmethod
    def list_pending(
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Revision], int]:
        """Return all pending revisions across all posts, oldest first (review queue)."""
        q = (
            select(Revision)
            .where(Revision.status == RevisionStatus.pending)
            .order_by(Revision.created_at.asc())
        )
        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        revisions = list(
            db.session.scalars(q.offset((page - 1) * per_page).limit(per_page))
        )
        return revisions, total

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_diff(base: str, proposed: str) -> str:
        """Return a unified diff string comparing *base* to *proposed*."""
        base_lines = base.splitlines(keepends=True)
        proposed_lines = proposed.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                base_lines,
                proposed_lines,
                fromfile="original",
                tofile="proposed",
                lineterm="",
            )
        )
