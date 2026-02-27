"""API routes for reporting posts and comments."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.services.report_service import ReportError, ReportService
from backend.utils.auth import api_require_auth, api_require_role, get_current_user

api_reports_bp = Blueprint("api_reports", __name__, url_prefix="/api/reports")


@api_reports_bp.post("/<string:target_type>/<int:target_id>")
@api_require_auth
def submit_report(target_type: str, target_id: int):
    """POST /api/reports/post/<id>  or  POST /api/reports/comment/<id>"""
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    note = (data.get("note") or "").strip() or None

    try:
        report = ReportService.submit(
            reporter_id=get_current_user().id,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            note=note,
        )
    except ReportError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify({"id": report.id, "status": report.status}), 201


@api_reports_bp.post("/<int:report_id>/resolve")
@api_require_role("editor")
def resolve_report(report_id: int):
    """POST /api/reports/<id>/resolve  — editor+"""
    try:
        report = ReportService.resolve(
            report_id=report_id,
            resolver_id=get_current_user().id,
            dismiss=False,
        )
    except ReportError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"id": report.id, "status": report.status})


@api_reports_bp.post("/<int:report_id>/dismiss")
@api_require_role("editor")
def dismiss_report(report_id: int):
    """POST /api/reports/<id>/dismiss  — editor+"""
    try:
        report = ReportService.resolve(
            report_id=report_id,
            resolver_id=get_current_user().id,
            dismiss=True,
        )
    except ReportError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"id": report.id, "status": report.status})
