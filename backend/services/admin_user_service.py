"""Admin user service — privileged user management operations."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from backend.models.portal import UserPrivacySettings
from backend.models.revision import Revision, RevisionStatus
from backend.models.user import User, UserRole

_PAGE_SIZE = 40


class AdminUserError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminUserService:
    @staticmethod
    def list_users(
        *,
        q: str | None = None,
        role: str | None = None,
        verified: bool | None = None,
        active: bool | None = None,
        sort: str = "created_desc",
        page: int = 1,
    ) -> tuple[list[User], int]:
        query = select(User)

        if q:
            like = f"%{q.lower()}%"
            query = query.where(
                or_(
                    User.username.ilike(like),
                    User.email.ilike(like),
                    User.display_name.ilike(like),
                )
            )
        if role:
            try:
                query = query.where(User.role == UserRole(role))
            except ValueError:
                pass
        if verified is not None:
            query = query.where(User.is_email_verified == verified)
        if active is not None:
            query = query.where(User.is_active == active)

        _SORT = {
            "created_desc": User.created_at.desc(),
            "created_asc": User.created_at.asc(),
            "username_asc": User.username.asc(),
            "rep_desc": User.reputation_score.desc(),
        }
        query = query.order_by(_SORT.get(sort, User.created_at.desc()))

        total = (
            db.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        )
        offset = (page - 1) * _PAGE_SIZE
        items = list(db.session.scalars(query.offset(offset).limit(_PAGE_SIZE)).all())
        return items, total

    @staticmethod
    def get_user_detail(user_id: int) -> dict | None:
        """Return a dict with everything an admin needs to see for a user."""
        user = db.session.get(User, user_id)
        if user is None:
            return None

        privacy = db.session.scalar(
            select(UserPrivacySettings).where(UserPrivacySettings.user_id == user_id)
        )

        rev_counts = {
            row[0]: row[1]
            for row in db.session.execute(
                select(Revision.status, func.count(Revision.id))
                .where(Revision.author_id == user_id)
                .group_by(Revision.status)
            ).all()
        }

        recent_revisions = list(
            db.session.scalars(
                select(Revision)
                .where(Revision.author_id == user_id)
                .options(joinedload(Revision.post))
                .order_by(Revision.created_at.desc())
                .limit(10)
            ).all()
        )

        return {
            "user": user,
            "privacy": privacy,
            "revision_counts": {
                "pending": rev_counts.get(RevisionStatus.pending, 0),
                "accepted": rev_counts.get(RevisionStatus.accepted, 0),
                "rejected": rev_counts.get(RevisionStatus.rejected, 0),
            },
            "recent_revisions": recent_revisions,
        }

    @staticmethod
    def set_active(user: User, active: bool, actor: User) -> None:
        if actor.id == user.id:
            raise AdminUserError("You cannot suspend your own account.")
        if user.role == UserRole.admin and actor.role != UserRole.admin:
            raise AdminUserError("Only admins can suspend other admins.")
        user.is_active = active
        db.session.commit()

    @staticmethod
    def set_role(user: User, new_role: UserRole, actor: User) -> None:
        if actor.role != UserRole.admin:
            raise AdminUserError("Only admins can change user roles.")
        if actor.id == user.id:
            raise AdminUserError("You cannot change your own role.")
        user.role = new_role
        db.session.commit()

    @staticmethod
    def verify_email(user: User) -> None:
        user.is_email_verified = True
        db.session.commit()

    @staticmethod
    def set_shadow_ban(user: User, banned: bool, actor: User) -> None:
        if actor.role != UserRole.admin:
            raise AdminUserError("Only admins can shadow-ban users.")
        user.is_shadow_banned = banned
        db.session.commit()
