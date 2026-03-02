"""Central permission service for OpenBlog.

All access-control decisions for posts, workspaces, and revisions flow
through :class:`PermissionService`.  Routes and services call these methods
and abort/raise appropriately — they do not re-implement logic inline.

Fail-closed contract
--------------------
Every method returns ``False`` (not an exception) when the answer is "no".
Callers decide whether to raise ``NotFound``, ``Forbidden``, or silently
skip the resource.  Workspace resources MUST use ``404`` — never ``403`` —
to avoid leaking existence to non-members.

Scope model
-----------
``post.workspace_id IS NULL``  → public post
``post.workspace_id IS NOT NULL`` → private workspace post

Public posts (published, workspace_id NULL): visible to everyone.
Public posts (draft, workspace_id NULL): visible only to author/admin.
Workspace posts: visible only to workspace members (any role).

INV-001 public content = workspace_id IS NULL AND status == published
                         AND published_at IS NOT NULL
This class does **not** enforce DB query filters — that is the
responsibility of service-layer query builders.  This class enforces
*object-level* (already-loaded resource) permission checks.
"""

from __future__ import annotations

from backend.models.post import Post, PostStatus
from backend.models.workspace import Workspace
from backend.security.roles import has_minimum_rank


def _is_admin(user) -> bool:  # type: ignore[return]
    """Return True if the platform user is a site admin."""
    return user is not None and getattr(user, "role", None) is not None and user.role.value == "admin"


def _workspace_member_role(user, workspace: Workspace) -> str | None:
    """Return the raw role string for *user* in *workspace*, or ``None``."""
    if user is None:
        return None
    # Late import to avoid circular deps at module load time.
    from sqlalchemy import select  # noqa: PLC0415

    from backend.extensions import db  # noqa: PLC0415
    from backend.models.workspace import WorkspaceMember  # noqa: PLC0415

    member = db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )
    return member.role.value if member is not None else None


class PermissionService:
    """Static-method service for access-control decisions.

    All methods accept a *user* that may be ``None`` (anonymous request).
    """

    # ── Workspace level ───────────────────────────────────────────────────

    @staticmethod
    def can_view_workspace(user, workspace: Workspace) -> bool:
        """Any workspace member (including viewer) may view."""
        if _is_admin(user):
            return True
        role = _workspace_member_role(user, workspace)
        return role is not None  # any membership = access

    @staticmethod
    def can_manage_workspace(user, workspace: Workspace) -> bool:
        """Owner or platform admin may manage workspace settings."""
        if _is_admin(user):
            return True
        role = _workspace_member_role(user, workspace)
        return has_minimum_rank(role or "", "owner")

    @staticmethod
    def can_manage_members(user, workspace: Workspace) -> bool:
        """Owner or platform admin may add/remove/change member roles."""
        return PermissionService.can_manage_workspace(user, workspace)

    # ── Post level ────────────────────────────────────────────────────────

    @staticmethod
    def can_view_post(user, post: Post) -> bool:
        """Determine whether *user* may view *post*.

        Rules
        -----
        - Public / published (workspace_id NULL, status published): anyone.
        - Public / draft: only the author or a platform admin.
        - Workspace post: any workspace member (any role).
        """
        if post.workspace_id is None:
            # Public layer
            if post.status == PostStatus.published:
                return True
            # Draft — author or admin only.
            if _is_admin(user):
                return True
            return user is not None and post.author_id == user.id

        # Workspace layer — any member.
        if _is_admin(user):
            return True
        role = _workspace_member_role(user, post.workspace)  # type: ignore[arg-type]
        return role is not None

    @staticmethod
    def can_create_post(user, workspace: Workspace | None = None) -> bool:
        """Determine whether *user* may create a new post.

        For public posts: platform contributor/editor/admin.
        For workspace posts: workspace editor/owner or platform admin.
        """
        if user is None:
            return False
        if _is_admin(user):
            return True
        if workspace is None:
            # Public post — requires contributor+ platform role.
            return user.role.value in ("admin", "editor", "contributor")
        # Workspace post — requires workspace editor+ role.
        role = _workspace_member_role(user, workspace)
        return has_minimum_rank(role or "", "editor")

    @staticmethod
    def can_edit_post(user, post: Post) -> bool:
        """Determine whether *user* may edit *post*.

        Public post: author or platform admin/editor.
        Workspace post: workspace editor/owner or platform admin.
        """
        if user is None:
            return False
        if _is_admin(user):
            return True
        if post.workspace_id is None:
            # Public post: author or platform editor.
            if post.author_id == user.id:
                return True
            return user.role.value in ("admin", "editor")
        # Workspace post.
        role = _workspace_member_role(user, post.workspace)  # type: ignore[arg-type]
        return has_minimum_rank(role or "", "editor")

    @staticmethod
    def can_suggest_revision(user, post: Post) -> bool:
        """Determine whether *user* may submit a revision proposal.

        Public post: any authenticated contributor+ user.
        Workspace post: workspace contributor+ member.
        """
        if user is None:
            return False
        if _is_admin(user):
            return True
        if post.workspace_id is None:
            return user.role.value in ("admin", "editor", "contributor")
        role = _workspace_member_role(user, post.workspace)  # type: ignore[arg-type]
        return has_minimum_rank(role or "", "contributor")

    @staticmethod
    def can_accept_revision(user, post: Post) -> bool:
        """Determine whether *user* may accept a pending revision.

        Public post: author or platform admin/editor.
        Workspace post: workspace editor/owner or platform admin.
        """
        if user is None:
            return False
        if _is_admin(user):
            return True
        if post.workspace_id is None:
            if post.author_id == user.id:
                return True
            return user.role.value in ("admin", "editor")
        role = _workspace_member_role(user, post.workspace)  # type: ignore[arg-type]
        return has_minimum_rank(role or "", "editor")

    @staticmethod
    def can_publish_post(user, post: Post) -> bool:
        """Determine whether *user* may publish *post*.

        The rules mirror :meth:`can_edit_post`.
        """
        return PermissionService.can_edit_post(user, post)

    @staticmethod
    def can_delete_post(user, post: Post) -> bool:
        """Determine whether *user* may delete *post*.

        Public post: author or platform admin.
        Workspace post: workspace owner or platform admin.
        """
        if user is None:
            return False
        if _is_admin(user):
            return True
        if post.workspace_id is None:
            return post.author_id == user.id
        role = _workspace_member_role(user, post.workspace)  # type: ignore[arg-type]
        return has_minimum_rank(role or "", "owner")

    @staticmethod
    def can_clone_to_public(user, post: Post) -> bool:
        """Determine whether *user* may clone a workspace document to a public draft.

        Clone-to-public copies a workspace post into a brand-new public draft
        (workspace_id NULL, status = draft).  It never auto-publishes, so the
        clone itself is never immediately public.

        Only workspace owner/editor or platform admin may clone.
        Contributors and viewers cannot expose workspace content.
        """
        if user is None:
            return False
        if _is_admin(user):
            return True
        if post.workspace_id is None:
            # Already public — no clone needed; permission not applicable.
            return False
        role = _workspace_member_role(user, post.workspace)  # type: ignore[arg-type]
        return has_minimum_rank(role or "", "editor")
