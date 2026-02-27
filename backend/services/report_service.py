"""Report service — submit and manage moderation reports."""

from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.report import Report


class ReportError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_VALID_TYPES = frozenset({"post", "comment"})
_VALID_REASONS = frozenset({
    "spam", "harassment", "misinformation", "off_topic", "other"
})


class ReportService:
    @staticmethod
    def submit(
        reporter_id: int,
        target_type: str,
        target_id: int,
        reason: str,
        note: str | None = None,
    ) -> Report:
        """Submit a new report.

        Raises
        ------
        ReportError(400)  invalid target_type or reason
        ReportError(409)  reporter already has an open report for this target
        """
        if target_type not in _VALID_TYPES:
            raise ReportError(f"Invalid target type: {target_type!r}.", 400)
        if reason not in _VALID_REASONS:
            raise ReportError(f"Invalid reason: {reason!r}.", 400)

        # One-open-report-per-target uniqueness check (service layer)
        existing = db.session.scalar(
            select(Report).where(
                Report.reporter_id == reporter_id,
                Report.target_type == target_type,
                Report.target_id == target_id,
                Report.status == "open",
            )
        )
        if existing is not None:
            raise ReportError(
                "You already have an open report for this item.", 409
            )

        report = Report(
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            note=(note or "").strip() or None,
        )
        db.session.add(report)
        db.session.commit()
        return report

    @staticmethod
    def resolve(report_id: int, resolver_id: int, *, dismiss: bool = False) -> Report:
        """Mark a report as resolved or dismissed."""
        report = db.session.get(Report, report_id)
        if report is None:
            raise ReportError("Report not found.", 404)
        report.status = "dismissed" if dismiss else "resolved"
        report.resolved_by_id = resolver_id
        db.session.commit()
        return report

    @staticmethod
    def list_reports(
        *,
        status: str | None = "open",
        target_type: str | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[list[Report], int]:
        """Return ``(reports, total)`` for the given filters.

        Parameters
        ----------
        status:
            ``"open"``, ``"resolved"``, ``"dismissed"``, or ``None`` / ``"all"``
            to skip the status filter.
        target_type:
            ``"post"``, ``"comment"``, or ``None`` for all.
        page:
            1-based page number.
        per_page:
            Rows per page (default 30).
        """
        q = (
            select(Report)
            .options(joinedload(Report.reporter), joinedload(Report.resolver))
            .order_by(desc(Report.created_at))
        )
        if status and status != "all":
            q = q.where(Report.status == status)
        if target_type:
            q = q.where(Report.target_type == target_type)

        total = db.session.scalar(
            select(func.count()).select_from(q.subquery())
        ) or 0
        offset = (page - 1) * per_page
        reports = list(
            db.session.execute(q.offset(offset).limit(per_page)).unique().scalars()
        )
        return reports, total

    @staticmethod
    def open_count() -> int:
        """Return the number of currently open reports (for the sidebar badge)."""
        return db.session.scalar(
            select(func.count(Report.id)).where(Report.status == "open")
        ) or 0
