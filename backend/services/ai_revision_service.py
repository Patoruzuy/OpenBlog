"""AI Suggestion-to-Revision Service — workspace-scoped.

Converts a structured AI suggested edit (from ``AIReviewResult.suggested_edits_json``)
into a pending :class:`~backend.models.revision.Revision` proposal so that a
workspace member can create an actionable edit from an AI finding without the
AI ever touching the document directly.

Human-in-the-loop guarantee
-----------------------------
This service only **proposes** a revision (``status=pending``).  The AI never
auto-merges and never silently alters content.  An editor or owner must
explicitly accept the resulting revision via the standard revision workflow
(:meth:`~backend.services.revision_service.RevisionService.accept`).

Supported edit kinds
---------------------
``replace_block``
    Exact-text replacement.  Finds the first occurrence of
    ``target_hint["match"]`` in the document and replaces it with
    ``proposed_markdown``.  **Fails with 400** if the target substring is
    not present.

``insert_after_heading``
    Inserts ``proposed_markdown`` on the line immediately after the first
    Markdown heading whose stripped text matches ``target_hint["heading"]``.
    A heading is any line whose content (after stripping leading ``#``
    characters and whitespace) equals the target heading string.
    **Fails with 400** if no matching heading is found.

``append_block``
    Appends ``proposed_markdown`` after the last line of the document.
    Always succeeds regardless of document content.

Workspace-only in v1
---------------------
Workspace documents are in ``draft`` status and therefore cannot go through
:meth:`~backend.services.revision_service.RevisionService.submit` (which
requires a published post).  This service creates the revision directly,
reusing the same data model and diff computation but skipping the published
and author-exclusion guards that apply to the public layer.

Permission rule
---------------
The caller must hold at least the ``contributor`` role in the workspace that
owns the document.  Viewers may not create revisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.extensions import db
from backend.models.ai_review import AIReviewRequest, AIReviewResult, AIReviewStatus
from backend.models.post import Post
from backend.models.post_version import PostVersion
from backend.models.revision import Revision, RevisionStatus
from backend.models.workspace import WorkspaceMember, WorkspaceMemberRole

if TYPE_CHECKING:
    from backend.models.user import User


# ── Domain error ──────────────────────────────────────────────────────────────


class AIRevisionError(Exception):
    """Raised by this service for business-rule violations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── Edit application ──────────────────────────────────────────────────────────


def _apply_edit(markdown: str, edit: dict) -> str:
    """Apply one structured edit operation to *markdown* and return the result.

    Parameters
    ----------
    markdown:
        The current document body.
    edit:
        One element from ``suggested_edits_json["edits"]``.

    Returns
    -------
    str
        The modified document body.

    Raises
    ------
    AIRevisionError 400
        ``replace_block`` — target substring not found.
        ``insert_after_heading`` — heading not found.
        Unknown ``kind``.
    """
    kind: str = edit.get("kind", "")
    target_hint: dict = edit.get("target_hint") or {}
    proposed: str = edit.get("proposed_markdown", "")

    if kind == "replace_block":
        match_text: str = target_hint.get("match", "")
        if not match_text:
            raise AIRevisionError(
                "replace_block edit is missing target_hint.match.",
                status_code=400,
            )
        if match_text not in markdown:
            raise AIRevisionError(
                f"Target text not found in document: {match_text[:120]!r}",
                status_code=400,
            )
        # Replace only the first occurrence to stay predictable.
        return markdown.replace(match_text, proposed, 1)

    if kind == "append_block":
        # Always succeeds; ensure a blank-line separator.
        return markdown.rstrip("\n") + "\n\n" + proposed.lstrip("\n")

    if kind == "insert_after_heading":
        heading_target: str = target_hint.get("heading", "")
        if not heading_target:
            raise AIRevisionError(
                "insert_after_heading edit is missing target_hint.heading.",
                status_code=400,
            )
        lines = markdown.splitlines(keepends=True)
        insert_at: int | None = None
        for i, line in enumerate(lines):
            # Strip leading '#' chars and surrounding whitespace to get the text.
            stripped = line.lstrip("#").strip()
            if stripped == heading_target:
                insert_at = i + 1
                break
        if insert_at is None:
            raise AIRevisionError(
                f"Heading not found in document: {heading_target!r}",
                status_code=400,
            )
        # Ensure proposed block starts with a newline.
        block = proposed if proposed.startswith("\n") else "\n" + proposed
        lines.insert(insert_at, block)
        return "".join(lines)

    raise AIRevisionError(
        f"Unknown edit kind {kind!r}. Supported: replace_block, append_block, "
        "insert_after_heading.",
        status_code=400,
    )


# ── Permission helpers ────────────────────────────────────────────────────────


def _require_workspace_contributor(user: User, post: Post) -> WorkspaceMember:
    """Return the workspace membership row or raise :class:`AIRevisionError`.

    Raises
    ------
    AIRevisionError 404
        Post not in a workspace, or caller has no workspace membership.
    AIRevisionError 403
        Caller's role is below ``contributor`` (i.e. viewer).
    """
    if post.workspace_id is None:
        raise AIRevisionError(
            "AI suggestion-to-revision is only available for workspace documents.",
            status_code=404,
        )
    member = db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == post.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if member is None:
        raise AIRevisionError("Not found.", status_code=404)
    if not member.role.meets(WorkspaceMemberRole.contributor):
        raise AIRevisionError(
            "Only workspace contributors and above can create revision proposals.",
            status_code=403,
        )
    return member


# ── Workspace-aware revision creation ────────────────────────────────────────


def _create_workspace_revision(
    *,
    post: Post,
    author: User,
    proposed_markdown: str,
    summary: str,
    source_metadata: dict | None = None,
) -> Revision:
    """Create and flush a pending Revision for a workspace document.

    Unlike :meth:`~backend.services.revision_service.RevisionService.submit`
    this function does **not** require ``post.status == published`` (workspace
    documents are always ``draft``) and does **not** block the post's author
    (workspace owners are entitled to benefit from AI suggestions too).

    Callers must commit after this returns.
    """
    from backend.services.revision_service import RevisionService  # noqa: PLC0415

    if proposed_markdown.strip() == (post.markdown_body or "").strip():
        raise AIRevisionError(
            "The AI suggestion produces no change from the current document body.",
            status_code=400,
        )

    # Snapshot: resolve the latest accepted PostVersion as the base.
    latest_version: PostVersion | None = db.session.scalar(
        select(PostVersion)
        .where(PostVersion.post_id == post.id)
        .order_by(PostVersion.version_number.desc())
        .limit(1)
    )
    base_markdown = (
        latest_version.markdown_body
        if latest_version is not None
        else post.markdown_body or ""
    )

    diff_cache = RevisionService._compute_diff(base_markdown, proposed_markdown)

    revision = Revision(
        post_id=post.id,
        author_id=author.id,
        base_version_id=latest_version.id if latest_version else None,
        base_version_number=post.version,
        proposed_markdown=proposed_markdown,
        summary=summary,
        diff_cache=diff_cache,
        status=RevisionStatus.pending,
        source_metadata_json=source_metadata,
    )
    db.session.add(revision)
    db.session.flush()
    return revision


# ── Public API ────────────────────────────────────────────────────────────────


def create_revision_from_ai_suggestion(
    user: User,
    post: Post,
    ai_review_request_id: int,
    suggestion_id: str,
) -> Revision:
    """Convert a single AI suggested edit into a pending Revision proposal.

    Steps
    -----
    1.  Verify workspace membership (contributor+).
    2.  Load :class:`~backend.models.ai_review.AIReviewRequest`; verify it
        belongs to *post*, its status is ``completed``, and it carries a
        ``result`` with ``suggested_edits_json``.
    3.  Locate the edit whose ``id`` matches *suggestion_id*.
    4.  Apply the edit operation to the current ``post.markdown_body``.
    5.  Create the :class:`~backend.models.revision.Revision` with
        ``source_metadata_json`` recording the AI provenance.
    6.  Commit and return.

    Parameters
    ----------
    user:
        The workspace member initiating the action.
    post:
        The workspace document the review belongs to.
    ai_review_request_id:
        Primary key of the :class:`~backend.models.ai_review.AIReviewRequest`.
    suggestion_id:
        The ``id`` field of the targeted edit inside ``suggested_edits_json``.

    Returns
    -------
    Revision
        A newly created ``pending`` revision with source attribution.

    Raises
    ------
    AIRevisionError 404
        Review not found, does not belong to this post/workspace, or caller
        is not a workspace member.
    AIRevisionError 400
        Review is not completed, suggestion_id not found, no suggested edits,
        edit application failed (target not found), or proposed text is
        identical to the current body.
    AIRevisionError 403
        Caller's workspace role is below ``contributor``.
    """
    # 1. Workspace gate.
    _require_workspace_contributor(user, post)

    # 2. Load & validate the review request.
    req: AIReviewRequest | None = db.session.get(AIReviewRequest, ai_review_request_id)
    if req is None or req.post_id != post.id:
        raise AIRevisionError("AI review not found for this document.", status_code=404)
    if req.workspace_id != post.workspace_id:
        raise AIRevisionError("AI review not found.", status_code=404)
    if req.status != AIReviewStatus.completed.value:
        raise AIRevisionError(
            f"AI review is not completed (current status: {req.status!r}). "
            "Wait for the review to finish before creating a revision.",
            status_code=400,
        )

    # Load the result (eager if already in session, else fetch).
    result: AIReviewResult | None = db.session.scalar(
        select(AIReviewResult).where(AIReviewResult.request_id == req.id)
    )
    if result is None:
        raise AIRevisionError(
            "AI review result not found — the review may not have completed correctly.",
            status_code=400,
        )

    edits: list[dict] = (result.suggested_edits_json or {}).get("edits", [])
    if not edits:
        raise AIRevisionError(
            "This AI review contains no suggested edits.",
            status_code=400,
        )

    # 3. Locate the requested suggestion.
    edit: dict | None = next((e for e in edits if e.get("id") == suggestion_id), None)
    if edit is None:
        raise AIRevisionError(
            f"Suggestion {suggestion_id!r} not found in this AI review.",
            status_code=400,
        )

    # 4. Apply the edit operation to the current post body.
    proposed_markdown = _apply_edit(post.markdown_body or "", edit)

    # 5. Build a human-readable summary and source metadata.
    edit_title: str = edit.get("title", suggestion_id)
    summary = (
        f"[AI suggestion] {edit_title} "
        f"(review #{ai_review_request_id}, suggestion {suggestion_id!r})"
    )
    source_metadata = {
        "source": "ai_suggestion",
        "ai_review_request_id": ai_review_request_id,
        "suggestion_id": suggestion_id,
    }

    # 6. Create the revision and commit.
    revision = _create_workspace_revision(
        post=post,
        author=user,
        proposed_markdown=proposed_markdown,
        summary=summary,
        source_metadata=source_metadata,
    )
    db.session.commit()
    return revision
