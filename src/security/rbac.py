"""
Role-Based Access Control — FR-SEC-01.

Enforces role-based permissions (view/edit/approve/deploy) at the pipeline
level. This module sits in FRONT of existing tools and restricts access to
them by role — it does NOT replace or weaken any existing gate.

Key design principle (from phase instructions):
  A permission check wraps ACCESS to an action — it must NEVER become a
  second path that could grant or bypass approval. RBAC is a stricter,
  additive precondition. It runs BEFORE the existing is_safe/approved
  checks in deploy_gate.py, never INSTEAD of them.

Identity pattern: reused from Phase 8's proposer/reviewer concept — an
"actor" is a plain string identity (e.g., "alice", "bob@example.com"),
not a new identity abstraction.

Roles and permissions (FSD Section 8):
  viewer       — view only
  editor       — view + edit (own branches)
  approver     — view + edit + approve (HITL/proposal review)
  deployer     — view + deploy (explicitly does NOT auto-grant approve)
  admin        — all permissions

Note: deployer does NOT imply approver — they are separate permission sets.
This matches the FSD's role matrix where Data Engineer has both, but the
permission columns are distinct.

FSD requirements: FR-SEC-01 (Role-based access control, M)
"""

from enum import Enum
from functools import wraps
from typing import Callable, Any


class Permission(str, Enum):
    """The atomic permissions enforceable at the pipeline level."""
    VIEW = "view"           # read pipeline state, schema, output preview
    EDIT = "edit"           # edit pipeline logic on a branch
    APPROVE = "approve"     # approve HITL proposals / version proposals
    DEPLOY = "deploy"       # deploy pipeline output to production
    MANAGE_USERS = "manage_users"  # assign/change roles (admin only)


class Role(str, Enum):
    """
    Roles as defined in FSD Section 8.

    Each role maps to an explicit, fixed set of permissions. Permissions
    are NOT inherited cumulatively except for admin — deployer does not
    auto-grant approve, and approver does not auto-grant deploy.
    """
    VIEWER = "viewer"
    EDITOR = "editor"
    APPROVER = "approver"
    DEPLOYER = "deployer"
    ADMIN = "admin"


# ---------------------------------------------------------------------------
# Role -> Permission mapping (FSD Section 8)
# This is the single source of truth for what each role can do.
# Deliberately explicit: deployer does NOT include APPROVE, approver does
# NOT include DEPLOY. Only admin has everything.
# ---------------------------------------------------------------------------
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({
        Permission.VIEW,
    }),
    Role.EDITOR: frozenset({
        Permission.VIEW,
        Permission.EDIT,
    }),
    Role.APPROVER: frozenset({
        Permission.VIEW,
        Permission.EDIT,
        Permission.APPROVE,
    }),
    Role.DEPLOYER: frozenset({
        Permission.VIEW,
        Permission.DEPLOY,
    }),
    Role.ADMIN: frozenset({
        Permission.VIEW,
        Permission.EDIT,
        Permission.APPROVE,
        Permission.DEPLOY,
        Permission.MANAGE_USERS,
    }),
}


class AccessDeniedError(PermissionError):
    """
    Raised when an actor lacks the required permission.

    This is a PermissionError subclass so it is caught by existing
    except PermissionError handlers if any exist, but carries structured
    detail for audit logging.
    """
    def __init__(self, actor: str, role: str, required_permission: str, action: str = ""):
        self.actor = actor
        self.role = role
        self.required_permission = required_permission
        self.action = action
        msg = (
            f"Access denied: actor '{actor}' with role '{role}' lacks permission "
            f"'{required_permission}'"
        )
        if action:
            msg += f" for action '{action}'"
        super().__init__(msg)


class RBACManager:
    """
    Manages actor-to-role assignments and permission checks.

    An "actor" is a plain string identity (reusing Phase 8's proposer/reviewer
    pattern — no new identity abstraction). The manager maps actors to roles
    and enforces that an actor's role has the required permission before an
    action proceeds.

    Usage:
        rbac = RBACManager()
        rbac.assign_role("alice", Role.DEPLOYER)
        rbac.check_permission("alice", Permission.DEPLOY, action="deploy_pipeline")
        # passes — alice is a deployer

        rbac.assign_role("bob", Role.VIEWER)
        rbac.check_permission("bob", Permission.DEPLOY, action="deploy_pipeline")
        # raises AccessDeniedError — bob is a viewer

    The check_permission method is the core enforcement point. It is meant
    to be called at the TOP of an MCP tool function, BEFORE any existing
    gate (e.g., deploy_gate.py's is_safe/approved checks). It is a stricter,
    additive precondition — it never weakens or bypasses existing gates.
    """

    def __init__(self):
        self._actor_roles: dict[str, Role] = {}

    def assign_role(self, actor: str, role: Role) -> None:
        """
        Assign a role to an actor. Overwrites any previous role.

        Args:
            actor: the actor's identity string (e.g., "alice")
            role: the Role to assign

        Raises:
            ValueError: if actor is empty
        """
        if not actor or not actor.strip():
            raise ValueError("actor must not be empty — every actor needs a named identity")
        if not isinstance(role, Role):
            raise ValueError(f"role must be a Role enum, got {type(role).__name__}: {role}")
        self._actor_roles[actor] = role

    def get_role(self, actor: str) -> Role | None:
        """Return the actor's role, or None if the actor has no assignment."""
        return self._actor_roles.get(actor)

    def get_permissions(self, actor: str) -> frozenset[Permission]:
        """Return the set of permissions for the actor's role.
        Returns an empty frozenset if the actor has no role assigned."""
        role = self._actor_roles.get(actor)
        if role is None:
            return frozenset()
        return ROLE_PERMISSIONS.get(role, frozenset())

    def has_permission(self, actor: str, permission: Permission) -> bool:
        """Check if an actor has a specific permission. Returns bool, never raises."""
        return permission in self.get_permissions(actor)

    def check_permission(
        self,
        actor: str,
        permission: Permission,
        action: str = "",
    ) -> None:
        """
        Enforce that the actor has the required permission.

        This is the core enforcement point. It raises AccessDeniedError if
        the actor lacks the permission. It is meant to be called at the TOP
        of a tool function, BEFORE any existing gate.

        Args:
            actor: the actor's identity string
            permission: the required Permission
            action: optional human-readable name of the action being gated
                    (included in the error message and audit log for clarity)

        Raises:
            AccessDeniedError: if the actor lacks the permission
            ValueError: if actor is empty
        """
        if not actor or not actor.strip():
            raise ValueError("actor must not be empty — permission check requires a named actor")

        role = self._actor_roles.get(actor)
        if role is None:
            raise AccessDeniedError(
                actor=actor,
                role="unassigned",
                required_permission=permission.value,
                action=action,
            )

        allowed = ROLE_PERMISSIONS.get(role, frozenset())
        if permission not in allowed:
            raise AccessDeniedError(
                actor=actor,
                role=role.value,
                required_permission=permission.value,
                action=action,
            )

    def list_assignments(self) -> dict[str, str]:
        """Return a dict of actor -> role_name for all assignments."""
        return {actor: role.value for actor, role in self._actor_roles.items()}

    def summary(self) -> dict:
        """Return a summary of the RBAC state for MCP tool output."""
        return {
            "total_actors": len(self._actor_roles),
            "assignments": self.list_assignments(),
            "role_permissions": {
                role.value: [p.value for p in perms]
                for role, perms in ROLE_PERMISSIONS.items()
            },
        }


def require_permission(
    rbac: RBACManager,
    permission: Permission,
    actor_arg: str = "actor",
    action: str = "",
):
    """
    Decorator factory: enforce that the calling actor has a permission.

    Wraps a function so that the named argument (actor_arg) is checked
    against the RBAC manager before the function body runs. This is the
    enforcement decorator usable by MCP tools.

    Usage:
        @require_permission(_rbac, Permission.DEPLOY, actor_arg="deployed_by", action="deploy")
        def deploy(approved: bool, deployed_by: str = "", ...):
            ...

    The check runs BEFORE the function body — it is a stricter, additive
    precondition. It never weakens or bypasses any existing gate inside
    the function (e.g., deploy_gate.py's is_safe/approved checks).

    Args:
        rbac: the RBACManager instance to check against
        permission: the required Permission
        actor_arg: the name of the function argument holding the actor identity
        action: optional human-readable action name for the error/audit

    Raises:
        AccessDeniedError: if the actor lacks the permission (from check_permission)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            actor = kwargs.get(actor_arg)
            if actor is None:
                # Try positional args by inspecting the function signature
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if actor_arg in params:
                    idx = params.index(actor_arg)
                    if idx < len(args):
                        actor = args[idx]
            if actor is None or (isinstance(actor, str) and not actor.strip()):
                raise ValueError(
                    f"Actor argument '{actor_arg}' is required for permission check "
                    f"but was not provided or is empty."
                )
            rbac.check_permission(actor, permission, action=action or func.__name__)
            return func(*args, **kwargs)
        return wrapper
    return decorator
