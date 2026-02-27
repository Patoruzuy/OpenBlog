"""Repository Service.

CRUD + ordering for a user's ``UserRepository`` records (displayed on public
profile and the /settings/repositories settings page).

Currently supports manual entry only.  The GitHub sync path (OAuth-gated) is
stubbed out so it can be wired up once OAuth is in place.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.extensions import db
from backend.models.portal import RepositorySource, UserRepository
from backend.models.user import User
from backend.utils.validation import validate_url


class RepositoryServiceError(Exception):
    """Domain error raised by RepositoryService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RepositoryService:
    """CRUD and ordering for a user's repository list."""

    # ── Read ──────────────────────────────────────────────────────────────────

    @staticmethod
    def get_for_user(user_id: int, *, public_only: bool = False) -> list[UserRepository]:
        """Return all repositories for *user_id*, ordered by sort_order asc."""
        stmt = (
            select(UserRepository)
            .where(UserRepository.user_id == user_id)
            .order_by(UserRepository.sort_order, UserRepository.id)
        )
        if public_only:
            stmt = stmt.where(UserRepository.is_public.is_(True))
        return list(db.session.scalars(stmt))

    @staticmethod
    def get_by_id(repo_id: int, user_id: int) -> UserRepository:
        """Return a specific repository that belongs to *user_id*.

        Raises
        ------
        RepositoryServiceError(404) if not found or belongs to another user.
        """
        repo = db.session.get(UserRepository, repo_id)
        if repo is None or repo.user_id != user_id:
            raise RepositoryServiceError("Repository not found.", 404)
        return repo

    # ── Create ────────────────────────────────────────────────────────────────

    @staticmethod
    def add(
        user: User,
        *,
        repo_name: str,
        repo_url: str,
        description: str | None = None,
        language: str | None = None,
        is_featured: bool = False,
        is_public: bool = True,
    ) -> UserRepository:
        """Create a manual repository entry for *user*.

        Raises
        ------
        RepositoryServiceError(400) on validation failure.
        RepositoryServiceError(409) if the user already has a repo with this URL.
        """
        repo_name = repo_name.strip()
        if not repo_name:
            raise RepositoryServiceError("Repository name is required.")
        if len(repo_name) > 200:
            raise RepositoryServiceError("Repository name must be 200 characters or fewer.")

        repo_url_clean = validate_url(repo_url.strip(), field="repo_url")

        # Duplicate-URL check
        existing = db.session.scalar(
            select(UserRepository).where(
                UserRepository.user_id == user.id,
                UserRepository.repo_url == repo_url_clean,
            )
        )
        if existing is not None:
            raise RepositoryServiceError("You have already added this repository.", 409)

        # Sort order = one past the current max
        max_order = db.session.scalar(
            select(db.func.max(UserRepository.sort_order)).where(
                UserRepository.user_id == user.id
            )
        )
        sort_order = (max_order or 0) + 1

        repo = UserRepository(
            user_id=user.id,
            source=RepositorySource.manual,
            repo_name=repo_name,
            repo_url=repo_url_clean,
            description=(description or "").strip() or None,
            language=(language or "").strip() or None,
            is_featured=is_featured,
            is_public=is_public,
            sort_order=sort_order,
        )
        db.session.add(repo)
        db.session.commit()
        return repo

    # ── Update ────────────────────────────────────────────────────────────────

    @staticmethod
    def update(
        repo_id: int,
        user_id: int,
        *,
        repo_name: str | None = None,
        repo_url: str | None = None,
        description: str | None = None,
        language: str | None = None,
        is_featured: bool | None = None,
        is_public: bool | None = None,
    ) -> UserRepository:
        """Update fields on an existing repository.  Only non-None fields change."""
        repo = RepositoryService.get_by_id(repo_id, user_id)

        if repo_name is not None:
            v = repo_name.strip()
            if not v:
                raise RepositoryServiceError("Repository name is required.")
            repo.repo_name = v
        if repo_url is not None:
            repo.repo_url = validate_url(repo_url.strip(), field="repo_url")
        if description is not None:
            repo.description = description.strip() or None
        if language is not None:
            repo.language = language.strip() or None
        if is_featured is not None:
            repo.is_featured = is_featured
        if is_public is not None:
            repo.is_public = is_public

        db.session.commit()
        return repo

    # ── Delete ────────────────────────────────────────────────────────────────

    @staticmethod
    def delete(repo_id: int, user_id: int) -> None:
        """Delete a repository.

        Raises
        ------
        RepositoryServiceError(404) if not found or belongs to another user.
        """
        repo = RepositoryService.get_by_id(repo_id, user_id)
        db.session.delete(repo)
        db.session.commit()

    # ── Ordering ──────────────────────────────────────────────────────────────

    @staticmethod
    def reorder(user_id: int, ordered_ids: list[int]) -> None:
        """Re-apply sort_order from the given list of repository IDs.

        ``ordered_ids`` must contain only IDs belonging to *user_id*.

        Raises
        ------
        RepositoryServiceError(400) if any ID is invalid/not owned by the user.
        """
        existing = {
            r.id: r for r in RepositoryService.get_for_user(user_id)
        }
        for pos, repo_id in enumerate(ordered_ids):
            if repo_id not in existing:
                raise RepositoryServiceError(
                    f"Repository ID {repo_id} not found or does not belong to you.", 400
                )
            existing[repo_id].sort_order = pos
        db.session.commit()

    # ── GitHub sync stub ──────────────────────────────────────────────────────

    @staticmethod
    def sync_github(user: User) -> list[UserRepository]:  # pragma: no cover
        """Sync repositories from GitHub (requires connected account with token).

        This is a stub.  Full implementation requires the GitHub OAuth flow and
        a GitHub API client.  When implemented:

        1. Fetch the user's connected GitHub account from UserConnectedAccount.
        2. Decrypt the access token.
        3. Call GET https://api.github.com/user/repos?type=owner&sort=updated
        4. Upsert on (user_id, external_repo_id) — update name/description/stars/forks.
        5. Set synced_at = now() on all touched rows.
        6. Return the full list.
        """
        raise NotImplementedError(
            "GitHub sync requires an OAuth-connected account.  "
            "Implement after the GitHub OAuth flow is available."
        )
