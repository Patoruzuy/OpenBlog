"""Audit log service — writes and queries the admin action trail.

All privileged admin actions should call ``AuditLogService.log(...)``
so the entire action history is available for review.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import request
from sqlalchemy import desc, func, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.admin import AuditLog
from backend.models.user import User

_PAGE_SIZE = 50


class AuditLogService:
    @staticmethod
    def log(
        *,
        actor: User | None,
        action: str,
        target_type: str | None = None,
        target_id: int | None = None,
        target_repr: str | None = None,
        before: dict | None = None,
        after: dict | None = None,
        note: str | None = None,
    ) -> AuditLog:
        """Append one record to the audit log and flush (no commit).

        Commits are left to the caller so this can participate in a
        larger transaction.
        """
        entry = AuditLog(
            actor_id=actor.id if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_repr=target_repr,
            before_state=json.dumps(before) if before is not None else None,
            after_state=json.dumps(after) if after is not None else None,
            ip_address=_safe_ip(),
            note=note,
        )
        db.session.add(entry)
        return entry

    @staticmethod
    def list_entries(
        *,
        actor_id: int | None = None,
        action_prefix: str | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
    ) -> tuple[list[AuditLog], int]:
        """Return (entries, total_count) for the given filters."""
        q = (
            select(AuditLog)
            .options(joinedload(AuditLog.actor))
            .order_by(desc(AuditLog.created_at))
        )
        if actor_id is not None:
            q = q.where(AuditLog.actor_id == actor_id)
        if action_prefix:
            q = q.where(AuditLog.action.like(f"{action_prefix}%"))
        if target_type:
            q = q.where(AuditLog.target_type == target_type)
        if target_id is not None:
            q = q.where(AuditLog.target_id == target_id)
        if date_from:
            q = q.where(AuditLog.created_at >= date_from)
        if date_to:
            q = q.where(AuditLog.created_at <= date_to)

        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        offset = (page - 1) * _PAGE_SIZE
        items = list(
            db.session.scalars(q.offset(offset).limit(_PAGE_SIZE)).unique().all()
        )
        return items, total


def _safe_ip() -> str | None:
    try:
        return request.remote_addr
    except RuntimeError:
        return None
