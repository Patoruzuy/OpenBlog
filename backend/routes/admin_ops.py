"""Admin Ops Dashboard blueprint.

All routes require the ``admin`` role (super-admin only).

Route map
---------
GET   /admin/ops                           → health + 24-h snapshot
GET   /admin/ops/ai-reviews                → AI review requests (filterable)
POST  /admin/ops/ai-reviews/<id>/retry     → re-queue failed/canceled request
POST  /admin/ops/ai-reviews/<id>/cancel    → cancel queued/running request
GET   /admin/ops/digests                   → digest run history (filterable)
POST  /admin/ops/digests/<id>/retry        → re-enqueue failed digest
GET   /admin/ops/notifications             → notification aggregate stats

Security
--------
- All routes guarded by ``@require_admin`` — 403 for non-admin.
- ``Cache-Control: private, no-store`` on every response (no proxy caching).
- ``error_message`` fields are truncated to 400 chars in the service layer.
- No workspace content bodies are exposed — IDs and metadata only.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from backend.services.ops_service import (
    OpsError,
    _truncate_error,
    cancel_ai_review_request,
    get_ai_review_stats,
    get_health_snapshot,
    get_notification_stats,
    list_ai_review_requests,
    list_digest_runs,
    retry_ai_review_request,
    retry_digest_run,
)
from backend.utils.admin_auth import can, require_admin

admin_ops_bp = Blueprint("admin_ops", __name__, url_prefix="/admin/ops")


# ── Context processor (mirrors admin_bp so the shared layout works) ───────────


@admin_ops_bp.context_processor
def _admin_ops_context() -> dict:
    """Inject the same variables that admin_bp provides to the shared layout."""
    from sqlalchemy import func, select  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.revision import Revision, RevisionStatus  # noqa: PLC0415
    from backend.services.report_service import ReportService  # noqa: PLC0415

    pending = 0
    open_reports = 0
    try:
        pending = (
            db.session.scalar(
                select(func.count(Revision.id)).where(
                    Revision.status == RevisionStatus.pending
                )
            )
            or 0
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        open_reports = ReportService.open_count()
    except Exception:  # noqa: BLE001
        pass
    return {
        "can": can,
        "admin_pending_revisions": pending,
        "admin_open_reports": open_reports,
    }


def _no_store(response):
    """Set Cache-Control: private, no-store on *response*."""
    response.headers["Cache-Control"] = "private, no-store"
    return response


# ── Overview ──────────────────────────────────────────────────────────────────


@admin_ops_bp.route("")
@require_admin
def index():
    """Health snapshot + 24-hour async system counts."""
    health = get_health_snapshot()
    ai_stats = get_ai_review_stats(hours=24)

    # digest_runs counts (last 24 h) — status distribution.
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from sqlalchemy import func, select  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.digest_run import DigestRun  # noqa: PLC0415
    from backend.models.notification import Notification  # noqa: PLC0415

    cutoff = datetime.now(UTC) - timedelta(hours=24)

    digest_rows = db.session.execute(
        select(DigestRun.status, func.count(DigestRun.id))
        .where(DigestRun.period_start >= cutoff)
        .group_by(DigestRun.status)
    ).all()
    digest_stats: dict[str, int] = {"sent": 0, "skipped": 0, "failed": 0}
    for status_val, cnt in digest_rows:
        digest_stats[status_val] = cnt

    notif_count_24h: int = (
        db.session.scalar(
            select(func.count(Notification.id)).where(
                Notification.created_at >= cutoff
            )
        )
        or 0
    )

    resp = make_response(
        render_template(
            "admin/ops/index.html",
            health=health,
            ai_stats=ai_stats,
            digest_stats=digest_stats,
            notif_count_24h=notif_count_24h,
        )
    )
    return _no_store(resp)


# ── AI Reviews ────────────────────────────────────────────────────────────────


@admin_ops_bp.route("/ai-reviews")
@require_admin
def ai_reviews():
    """Filterable list of AI review requests (latest 100)."""
    status_filter = request.args.get("status", "").strip() or None
    ws_filter = request.args.get("workspace_id", type=int)

    requests_list = list_ai_review_requests(
        status=status_filter,
        workspace_id=ws_filter,
        limit=100,
    )

    # Truncate error messages at display time.
    for req in requests_list:
        if req.error_message:
            req.error_message = _truncate_error(req.error_message)

    from backend.models.ai_review import AIReviewStatus  # noqa: PLC0415

    resp = make_response(
        render_template(
            "admin/ops/ai_reviews.html",
            requests=requests_list,
            status_filter=status_filter or "",
            ws_filter=ws_filter or "",
            ai_statuses=[s.value for s in AIReviewStatus],
        )
    )
    return _no_store(resp)


@admin_ops_bp.route("/ai-reviews/<int:request_id>/retry", methods=["POST"])
@require_admin
def ai_review_retry(request_id: int):
    """Re-queue a failed or canceled AI review request."""
    try:
        retry_ai_review_request(request_id)
        flash(f"AI review #{request_id} has been re-queued.", "success")
    except OpsError as exc:
        flash(exc.message, "error")
    return redirect(_reviews_back())


@admin_ops_bp.route("/ai-reviews/<int:request_id>/cancel", methods=["POST"])
@require_admin
def ai_review_cancel(request_id: int):
    """Cancel a queued or running AI review request."""
    try:
        cancel_ai_review_request(request_id)
        flash(f"AI review #{request_id} has been canceled.", "success")
    except OpsError as exc:
        flash(exc.message, "error")
    return redirect(_reviews_back())


def _reviews_back() -> str:
    """Redirect back to the AI reviews list, preserving query string filters."""
    referrer = request.referrer or ""
    if "/admin/ops/ai-reviews" in referrer:
        return referrer
    return url_for("admin_ops.ai_reviews")


# ── Digests ───────────────────────────────────────────────────────────────────


@admin_ops_bp.route("/digests")
@require_admin
def digests():
    """Filterable list of digest runs (latest 100)."""
    status_filter = request.args.get("status", "").strip() or None
    runs = list_digest_runs(status=status_filter, limit=100)

    for run in runs:
        if run.error_message:
            run.error_message = _truncate_error(run.error_message)

    resp = make_response(
        render_template(
            "admin/ops/digests.html",
            runs=runs,
            status_filter=status_filter or "",
            digest_statuses=["sent", "skipped", "failed"],
        )
    )
    return _no_store(resp)


@admin_ops_bp.route("/digests/<int:digest_run_id>/retry", methods=["POST"])
@require_admin
def digest_retry(digest_run_id: int):
    """Re-enqueue a failed digest run."""
    try:
        retry_digest_run(digest_run_id)
        flash(f"Digest run #{digest_run_id} has been re-queued.", "success")
    except OpsError as exc:
        flash(exc.message, "error")
    return redirect(request.referrer or url_for("admin_ops.digests"))


# ── Notifications ─────────────────────────────────────────────────────────────


@admin_ops_bp.route("/notifications")
@require_admin
def notifications():
    """Aggregate notification statistics."""
    stats = get_notification_stats()
    resp = make_response(
        render_template("admin/ops/notifications.html", **stats)
    )
    return _no_store(resp)
