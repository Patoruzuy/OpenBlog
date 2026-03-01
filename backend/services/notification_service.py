"""Notification service — read and acknowledge in-app notifications.

Notifications are *written* by other services (e.g. UserService.follow writes
a 'new_follower' notification).  This service only handles the read path and
mark-as-read operations.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update

from backend.extensions import db
from backend.models.notification import Notification


class NotificationError(Exception):
    """Domain error raised by NotificationService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotificationService:
    """Static-method service for reading/acknowledging notifications."""

    @staticmethod
    def list_for_user(
        user_id: int,
        *,
        unread_only: bool = False,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Notification], int]:
        """Return paginated notifications for *user_id*, newest first.

        Parameters
        ----------
        unread_only:
            When True only unread notifications are returned.
        """
        q = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            q = q.where(Notification.is_read.is_(False))
        q = q.order_by(Notification.created_at.desc())

        total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
        notifications = list(
            db.session.scalars(q.offset((page - 1) * per_page).limit(per_page))
        )
        return notifications, total

    @staticmethod
    def mark_read(notification_id: int, user_id: int) -> Notification:
        """Mark a single notification as read.

        Raises
        ------
        NotificationError 404  notification not found or owned by a different user
        """
        notif = db.session.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        if notif is None:
            raise NotificationError("Notification not found.", 404)

        if not notif.is_read:
            notif.is_read = True
            notif.read_at = datetime.now(UTC)
            db.session.commit()

        return notif

    @staticmethod
    def mark_all_read(user_id: int) -> int:
        """Mark every unread notification for *user_id* as read.

        Returns the number of rows updated.
        """
        result = db.session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=datetime.now(UTC))
        )
        db.session.commit()
        return result.rowcount

    @staticmethod
    def unread_count(user_id: int) -> int:
        """Return the number of unread notifications for *user_id*."""
        return (
            db.session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
            )
            or 0
        )
