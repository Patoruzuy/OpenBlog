"""Release notes service — changelog entries for accepted post revisions.

Public API
----------
``get_post_release_notes(post_id)``
    Return all release notes for a post, newest-version first.
    Callers are responsible for ensuring the post is visible to the
    requesting user *before* calling this function — draft posts must
    not be exposed.

``create_release_note(...)``
    Create a single changelog entry.  Does **not** commit the session;
    the caller (``RevisionService.accept``) owns the transaction.

Design notes
------------
- Release notes are created *only* when ``RevisionService.accept`` runs
  successfully.  Draft/pending/rejected revisions never trigger creation.
- The ``summary`` field is always taken from ``Revision.summary`` (the
  human-supplied commit-message-style description that is required when
  submitting a revision).
- ``auto_generated`` is kept ``False`` for all revision-driven entries
  because the summary text is always human-supplied.  It is reserved for
  future programmatic note generation (e.g., post publish events).
- ``version_number`` is determined from ``Post.version`` *after* the post
  has already been incremented in ``RevisionService.accept``, so it always
  matches the ``PostVersion.version_number`` written in the same transaction.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.extensions import db
from backend.models.post_release_note import PostReleaseNote


def get_post_release_notes(post_id: int) -> list[PostReleaseNote]:
    """Return all changelog entries for *post_id*, ordered newest version first.

    Returns an empty list when no accepted revisions have been recorded yet.
    The caller must ensure the post is published before surfacing these to
    anonymous visitors.
    """
    return list(
        db.session.scalars(
            select(PostReleaseNote)
            .where(PostReleaseNote.post_id == post_id)
            .order_by(PostReleaseNote.version_number.desc())
        )
    )


def create_release_note(
    *,
    post_id: int,
    version_number: int,
    summary: str,
    accepted_revision_id: int | None = None,
    auto_generated: bool = False,
) -> PostReleaseNote:
    """Create and stage a new ``PostReleaseNote`` without committing.

    Parameters
    ----------
    post_id:
        The post this changelog entry belongs to.
    version_number:
        The *new* post version produced by the accepted revision.
        This matches ``Post.version`` *after* incrementing and mirrors
        ``PostVersion.version_number`` created in the same transaction.
    summary:
        One-line human-readable description of the change.
        When called from ``RevisionService.accept`` this is always
        ``revision.summary`` — the required commit message the contributor
        provided when submitting the revision.
    accepted_revision_id:
        FK back to the ``Revision`` record that triggered this entry.
        May be ``None`` for programmatically generated v1 entries.
    auto_generated:
        ``True`` when the summary was generated programmatically rather
        than supplied by a human.

    Returns
    -------
    PostReleaseNote
        The newly-created (unsaved) record.  It has been added to the
        SQLAlchemy session; the caller must ``db.session.commit()`` (or
        ``flush()``) to persist it.
    """
    note = PostReleaseNote(
        post_id=post_id,
        version_number=version_number,
        summary=summary,
        accepted_revision_id=accepted_revision_id,
        auto_generated=auto_generated,
    )
    db.session.add(note)
    return note
