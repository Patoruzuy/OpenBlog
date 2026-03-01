"""Role ranking for workspace membership and platform roles.

Two orthogonal role systems coexist:

Platform roles (User.role)
--------------------------
``admin`` > ``editor`` > ``contributor`` > ``reader``

Defined on :class:`~backend.models.user.UserRole` and enforced by the
existing ``@require_role`` decorator.  Admins can override most workspace
checks (see :mod:`backend.security.permissions`).

Workspace member roles (WorkspaceMember.role)
---------------------------------------------
``owner`` > ``editor`` > ``contributor`` > ``viewer``

Used exclusively inside workspace routes and :mod:`backend.services.workspace_service`.
The ``admin`` platform role functions as an implicit override, granting the
equivalent of ``owner`` access on any workspace.

ROLE_RANK
---------
A single integer rank table covers both systems (platform roles and workspace
member roles share the same namespace).  Only relative ordering matters.
"""

from __future__ import annotations

# Combined rank table.  Higher number = more privilege.
ROLE_RANK: dict[str, int] = {
    # Workspace member roles
    "viewer": 10,
    "contributor": 20,
    "editor": 30,
    "owner": 40,
    # Platform roles (used for the admin override)
    "reader": 5,
    "admin": 99,
}


def has_minimum_rank(role: str, minimum: str) -> bool:
    """Return ``True`` when *role* meets or exceeds *minimum* privilege.

    Roles not present in :data:`ROLE_RANK` are treated as rank 0 (denied).

    Examples
    --------
    >>> has_minimum_rank("editor", "editor")
    True
    >>> has_minimum_rank("viewer", "editor")
    False
    >>> has_minimum_rank("admin", "owner")
    True
    """
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum, 0)
