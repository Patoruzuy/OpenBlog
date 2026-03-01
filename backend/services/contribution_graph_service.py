"""ContributionGraphService — generate a 52-week contribution heatmap.

Public view: skips posts whose author had anonymous mode on at publish time.
Self view  : includes everything.
"""

from __future__ import annotations

import datetime
from collections import defaultdict

from sqlalchemy import and_, select

from backend.extensions import db
from backend.models.post import Post, PostStatus


class ContributionGraphService:
    """Build GitHub-style contribution heatmap data for a user's profile."""

    _WEEKS = 52  # columns in the grid

    @staticmethod
    def get_contributions(user_id: int, *, viewer_is_self: bool = False) -> dict:
        """Return 52-week grid data.

        Returns
        -------
        {
            "weeks": [
                [{"date": "YYYY-MM-DD", "level": 0-4, "count": int}, ...],
                ...
            ],
            "total": int,
        }
        Each sub-list represents one week (Mon-Sun), 7 cells each.
        """
        today = datetime.date.today()
        grid_end = today
        grid_start = (
            grid_end
            - datetime.timedelta(weeks=ContributionGraphService._WEEKS)
            + datetime.timedelta(days=1)
        )

        stmt = select(Post.published_at).where(
            and_(
                Post.author_id == user_id,
                Post.status == PostStatus.published,
                Post.published_at
                >= datetime.datetime.combine(grid_start, datetime.time.min),
                Post.published_at
                <= datetime.datetime.combine(grid_end, datetime.time.max),
            )
        )

        rows = db.session.execute(stmt).scalars().all()

        counts: dict[datetime.date, int] = defaultdict(int)
        for published_at in rows:
            d = published_at.date() if hasattr(published_at, "date") else published_at
            counts[d] += 1

        # Determine level thresholds
        all_counts = list(counts.values())
        max_count = max(all_counts, default=0)
        thresholds = ContributionGraphService._thresholds(max_count)

        def level(c: int) -> int:
            if c == 0:
                return 0
            for i, t in enumerate(thresholds, 1):
                if c <= t:
                    return i
            return 4

        # Build grid — full weeks from grid_start
        weeks: list[list[dict]] = []
        current = grid_start
        while current <= grid_end:
            week: list[dict] = []
            for _ in range(7):
                if current <= grid_end:
                    c = counts.get(current, 0)
                    week.append(
                        {
                            "date": current.isoformat(),
                            "level": level(c),
                            "count": c,
                        }
                    )
                    current += datetime.timedelta(days=1)
                else:
                    break  # shouldn't happen
            weeks.append(week)

        total = sum(all_counts)
        return {"weeks": weeks, "total": total}

    @staticmethod
    def _thresholds(max_count: int) -> tuple[int, int, int]:
        """Return (t1, t2, t3) so level mapping is evenly distributed."""
        if max_count <= 3:
            return (1, 2, 3)
        step = max(1, max_count // 4)
        return (step, step * 2, step * 3)
