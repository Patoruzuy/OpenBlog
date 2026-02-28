"""Read-history service — tracks which post versions each user has read.

Public API
----------
ReadHistoryService.record_read(user_id, post)
    Upsert a ``user_post_reads`` row for the given user and post, updating
    ``last_read_at`` and ``last_read_version`` to the current moment /
    current post version.  Called **after** the old record has been fetched
    (see ``get_read``) so that the detail view can show the pre-read version.

ReadHistoryService.get_read(user_id, post_id) -> UserPostRead | None
    Return the existing read record, or ``None`` if this is the first visit.

ReadHistoryService.get_updated_post_ids(user_id, posts) -> set[int]
    Given a list of Post objects, return the subset of their IDs whose
    ``version`` has increased since the user last read them.  Used by the
    post-list view to show "Updated" badges.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from backend.extensions import db
from backend.models.post import Post
from backend.models.user_post_read import UserPostRead


class ReadHistoryService:
    # ── Core upsert ────────────────────────────────────────────────────────

    @staticmethod
    def record_read(user_id: int, post: Post) -> UserPostRead:
        """Upsert the read record for *user_id* / *post*.

        Always updates ``last_read_at`` and ``last_read_version`` to the
        current moment and the post's current version respectively.
        """
        now = datetime.now(UTC)
        record = db.session.scalar(
            select(UserPostRead).where(
                UserPostRead.user_id == user_id,
                UserPostRead.post_id == post.id,
            )
        )
        if record is None:
            record = UserPostRead(
                user_id=user_id,
                post_id=post.id,
                last_read_at=now,
                last_read_version=post.version,
                created_at=now,
                updated_at=now,
            )
            db.session.add(record)
        else:
            record.last_read_at = now
            record.last_read_version = post.version
            record.updated_at = now
        db.session.commit()
        return record

    # ── Single-post query ──────────────────────────────────────────────────

    @staticmethod
    def get_read(user_id: int, post_id: int) -> UserPostRead | None:
        """Return the existing read record, or ``None`` on first visit."""
        return db.session.scalar(
            select(UserPostRead).where(
                UserPostRead.user_id == user_id,
                UserPostRead.post_id == post_id,
            )
        )

    # ── Bulk query for list views ──────────────────────────────────────────

    @staticmethod
    def get_updated_post_ids(user_id: int, posts: list[Post]) -> set[int]:
        """Return IDs of posts whose version exceeds the user's last-read version.

        Only posts the user has visited before can be "updated" — posts with
        no read record are silently excluded (they appear as simply unread).

        This issues a single ``WHERE post_id IN (…)`` query regardless of how
        many posts are in the list.
        """
        if not posts:
            return set()

        post_ids = [p.id for p in posts]
        records = db.session.scalars(
            select(UserPostRead).where(
                UserPostRead.user_id == user_id,
                UserPostRead.post_id.in_(post_ids),
            )
        ).all()

        # Map post_id → last_read_version for posts the user has visited
        read_map: dict[int, int] = {r.post_id: r.last_read_version for r in records}

        # Map post_id → current version (from the already-loaded Post objects)
        version_map: dict[int, int] = {p.id: p.version for p in posts}

        return {
            pid
            for pid, current_version in version_map.items()
            if pid in read_map and read_map[pid] < current_version
        }
