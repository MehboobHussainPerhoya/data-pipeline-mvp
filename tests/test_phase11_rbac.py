"""
Phase 11A tests — RBAC (FR-SEC-01).

Key test: a viewer-role actor attempting to call deploy is blocked by the
RBAC layer BEFORE ever reaching deploy_gate.py's own checks — and separately,
a deployer-role actor with approved=False is still blocked by deploy_gate.py
itself, proving the two layers are independent, not one replacing the other.
"""

import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from security.rbac import (
    Role, Permission, RBACManager, AccessDeniedError,
    ROLE_PERMISSIONS, require_permission,
)
from validation.deploy_gate import deploy_pipeline
from schema.output_schema import JoinedCaseOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(case_id="test_1", subject="test subject"):
    return JoinedCaseOutput(
        case_id=case_id,
        subject=subject,
        ticket_type="Technical issue",
        matched_category="CONTACT",
        matched_article_count=0,
        source_system="test",
    )


# ---------------------------------------------------------------------------
# Test Group 1: Role-permission mapping (FSD Section 8)
# ---------------------------------------------------------------------------

def test_viewer_has_only_view():
    perms = ROLE_PERMISSIONS[Role.VIEWER]
    assert Permission.VIEW in perms
    assert Permission.EDIT not in perms
    assert Permission.APPROVE not in perms
    assert Permission.DEPLOY not in perms


def test_editor_has_view_and_edit_only():
    perms = ROLE_PERMISSIONS[Role.EDITOR]
    assert Permission.VIEW in perms
    assert Permission.EDIT in perms
    assert Permission.APPROVE not in perms
    assert Permission.DEPLOY not in perms


def test_approver_has_view_edit_approve_but_not_deploy():
    perms = ROLE_PERMISSIONS[Role.APPROVER]
    assert Permission.VIEW in perms
    assert Permission.EDIT in perms
    assert Permission.APPROVE in perms
    assert Permission.DEPLOY not in perms


def test_deployer_has_view_deploy_but_not_approve():
    """CRITICAL: deployer does NOT auto-grant approve (FSD Section 8)."""
    perms = ROLE_PERMISSIONS[Role.DEPLOYER]
    assert Permission.VIEW in perms
    assert Permission.DEPLOY in perms
    assert Permission.APPROVE not in perms
    assert Permission.EDIT not in perms


def test_admin_has_all_permissions():
    perms = ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.VIEW in perms
    assert Permission.EDIT in perms
    assert Permission.APPROVE in perms
    assert Permission.DEPLOY in perms
    assert Permission.MANAGE_USERS in perms


# ---------------------------------------------------------------------------
# Test Group 2: RBACManager basic operations
# ---------------------------------------------------------------------------

def test_assign_and_check_permission():
    rbac = RBACManager()
    rbac.assign_role("alice", Role.DEPLOYER)
    assert rbac.has_permission("alice", Permission.DEPLOY) is True
    assert rbac.has_permission("alice", Permission.APPROVE) is False


def test_check_permission_passes_for_authorized_actor():
    rbac = RBACManager()
    rbac.assign_role("alice", Role.DEPLOYER)
    # Should not raise
    rbac.check_permission("alice", Permission.DEPLOY, action="deploy")


def test_check_permission_raises_for_unauthorized_actor():
    rbac = RBACManager()
    rbac.assign_role("bob", Role.VIEWER)
    with pytest.raises(AccessDeniedError) as exc_info:
        rbac.check_permission("bob", Permission.DEPLOY, action="deploy")
    assert "bob" in str(exc_info.value)
    assert "viewer" in str(exc_info.value)
    assert "deploy" in str(exc_info.value)


def test_check_permission_raises_for_unassigned_actor():
    rbac = RBACManager()
    with pytest.raises(AccessDeniedError) as exc_info:
        rbac.check_permission("unknown", Permission.VIEW, action="view")
    assert "unassigned" in str(exc_info.value)


def test_assign_empty_actor_raises():
    rbac = RBACManager()
    with pytest.raises(ValueError):
        rbac.assign_role("", Role.VIEWER)


def test_check_empty_actor_raises():
    rbac = RBACManager()
    with pytest.raises(ValueError):
        rbac.check_permission("", Permission.VIEW)


def test_get_role_returns_none_for_unassigned():
    rbac = RBACManager()
    assert rbac.get_role("nobody") is None


def test_get_permissions_empty_for_unassigned():
    rbac = RBACManager()
    assert rbac.get_permissions("nobody") == frozenset()


def test_overwrite_role():
    rbac = RBACManager()
    rbac.assign_role("alice", Role.VIEWER)
    assert rbac.has_permission("alice", Permission.DEPLOY) is False
    rbac.assign_role("alice", Role.DEPLOYER)
    assert rbac.has_permission("alice", Permission.DEPLOY) is True


# ---------------------------------------------------------------------------
# Test Group 3: THE CRITICAL TEST — two-layer independence
# This proves RBAC blocks a viewer BEFORE deploy_gate.py is ever reached,
# and deploy_gate.py independently blocks a deployer with approved=False.
# ---------------------------------------------------------------------------

def test_viewer_blocked_by_rbac_before_reaching_deploy_gate(tmp_path):
    """
    A viewer-role actor attempting to deploy is blocked by the RBAC layer
    BEFORE ever reaching deploy_gate.py's own checks.

    We prove "before" by asserting that deploy_gate.py's output file is
    never created and its RuntimeError (which mentions 'safety' or
    'approval') is never raised — the AccessDeniedError is raised first.
    """
    rbac = RBACManager()
    rbac.assign_role("viewer_user", Role.VIEWER)

    output_path = str(tmp_path / "output.json")
    records = [_make_record()]

    # Simulate the server.py deploy tool's RBAC-then-deploy sequence:
    # 1. RBAC check (this is what we're testing)
    # 2. deploy_pipeline() (deploy_gate.py — should never be reached)

    with pytest.raises(AccessDeniedError) as exc_info:
        rbac.check_permission("viewer_user", Permission.DEPLOY, action="deploy")
        # If we get here, RBAC failed to block — deploy_gate would run next:
        deploy_pipeline(
            records,
            is_safe=True,
            safety_reasons=[],
            approved=True,  # even with approved=True!
            output_path=output_path,
        )

    # The viewer was blocked by RBAC, not by deploy_gate
    assert "viewer_user" in str(exc_info.value)
    assert "viewer" in str(exc_info.value)
    assert "deploy" in str(exc_info.value)

    # CRITICAL: deploy_gate.py was never reached — no output file exists
    assert not Path(output_path).exists()


def test_deployer_with_approved_false_blocked_by_deploy_gate(tmp_path):
    """
    A deployer-role actor (who passes RBAC) with approved=False is still
    blocked by deploy_gate.py's own approval gate.

    This proves the two layers are independent: RBAC grants access to the
    deploy ACTION, but deploy_gate.py still independently requires
    is_safe=True AND approved=True. RBAC does not replace or weaken
    deploy_gate.py's gate.
    """
    rbac = RBACManager()
    rbac.assign_role("deployer_user", Role.DEPLOYER)

    output_path = str(tmp_path / "output.json")
    records = [_make_record()]

    # RBAC check passes (deployer has DEPLOY permission)
    rbac.check_permission("deployer_user", Permission.DEPLOY, action="deploy")

    # But deploy_gate.py still blocks because approved=False
    with pytest.raises(RuntimeError, match="approval"):
        deploy_pipeline(
            records,
            is_safe=True,
            safety_reasons=[],
            approved=False,
            output_path=output_path,
        )

    # deploy_gate blocked it — no output file
    assert not Path(output_path).exists()


def test_deployer_with_unsafe_output_blocked_by_deploy_gate(tmp_path):
    """
    A deployer-role actor (passes RBAC) with is_safe=False is blocked by
    deploy_gate.py's safety gate, not by RBAC.
    """
    rbac = RBACManager()
    rbac.assign_role("deployer_user", Role.DEPLOYER)

    output_path = str(tmp_path / "output.json")
    records = [_make_record()]

    # RBAC check passes
    rbac.check_permission("deployer_user", Permission.DEPLOY, action="deploy")

    # deploy_gate.py blocks because is_safe=False
    with pytest.raises(RuntimeError, match="safety"):
        deploy_pipeline(
            records,
            is_safe=False,
            safety_reasons=["Contract validation failed"],
            approved=True,
            output_path=output_path,
        )

    assert not Path(output_path).exists()


def test_deployer_with_safe_and_approved_succeeds(tmp_path):
    """
    A deployer-role actor with is_safe=True AND approved=True succeeds —
    both layers pass.
    """
    rbac = RBACManager()
    rbac.assign_role("deployer_user", Role.DEPLOYER)

    output_path = str(tmp_path / "output.json")
    records = [_make_record()]

    # RBAC check passes
    rbac.check_permission("deployer_user", Permission.DEPLOY, action="deploy")

    # deploy_gate.py passes because both conditions are met
    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
        deployed_by="deployer_user",
    )

    assert Path(output_path).exists()


def test_approver_cannot_deploy(tmp_path):
    """
    An approver (who can approve HITL/proposals) does NOT have deploy
    permission — deployer does not auto-grant from approver and vice versa.
    """
    rbac = RBACManager()
    rbac.assign_role("approver_user", Role.APPROVER)

    output_path = str(tmp_path / "output.json")
    records = [_make_record()]

    # RBAC blocks the approver from deploying
    with pytest.raises(AccessDeniedError):
        rbac.check_permission("approver_user", Permission.DEPLOY, action="deploy")
        deploy_pipeline(
            records,
            is_safe=True,
            safety_reasons=[],
            approved=True,
            output_path=output_path,
        )

    assert not Path(output_path).exists()


def test_deployer_cannot_approve():
    """
    A deployer does NOT have approve permission — the permissions are
    independent, not cumulative (except for admin).
    """
    rbac = RBACManager()
    rbac.assign_role("deployer_user", Role.DEPLOYER)

    with pytest.raises(AccessDeniedError):
        rbac.check_permission("deployer_user", Permission.APPROVE, action="approve_proposal")


# ---------------------------------------------------------------------------
# Test Group 4: require_permission decorator
# ---------------------------------------------------------------------------

def test_require_permission_decorator_blocks_unauthorized():
    """The decorator blocks an unauthorized actor before the function runs."""
    rbac = RBACManager()
    rbac.assign_role("bob", Role.VIEWER)

    @require_permission(rbac, Permission.DEPLOY, actor_arg="actor", action="deploy")
    def fake_deploy(actor: str, approved: bool = True):
        return "deployed"

    with pytest.raises(AccessDeniedError):
        fake_deploy(actor="bob")

    # The function body never ran — it didn't return "deployed"


def test_require_permission_decorator_allows_authorized():
    """The decorator allows an authorized actor to proceed."""
    rbac = RBACManager()
    rbac.assign_role("alice", Role.DEPLOYER)

    @require_permission(rbac, Permission.DEPLOY, actor_arg="actor", action="deploy")
    def fake_deploy(actor: str, approved: bool = True):
        return "deployed"

    result = fake_deploy(actor="alice")
    assert result == "deployed"


def test_require_permission_decorator_missing_actor_raises():
    """The decorator raises ValueError if the actor argument is missing."""
    rbac = RBACManager()

    @require_permission(rbac, Permission.DEPLOY, actor_arg="actor", action="deploy")
    def fake_deploy(approved: bool = True, actor: str = ""):
        return "deployed"

    with pytest.raises(ValueError, match="Actor argument"):
        fake_deploy()


def test_require_permission_decorator_positional_arg():
    """The decorator works with positional actor arguments too."""
    rbac = RBACManager()
    rbac.assign_role("alice", Role.DEPLOYER)

    @require_permission(rbac, Permission.DEPLOY, actor_arg="actor", action="deploy")
    def fake_deploy(actor: str, approved: bool = True):
        return f"deployed by {actor}"

    result = fake_deploy("alice")
    assert result == "deployed by alice"


# ---------------------------------------------------------------------------
# Test Group 5: RBAC summary / introspection
# ---------------------------------------------------------------------------

def test_summary():
    rbac = RBACManager()
    rbac.assign_role("alice", Role.DEPLOYER)
    rbac.assign_role("bob", Role.VIEWER)

    s = rbac.summary()
    assert s["total_actors"] == 2
    assert s["assignments"]["alice"] == "deployer"
    assert s["assignments"]["bob"] == "viewer"
    assert "deployer" in s["role_permissions"]
    assert "deploy" in s["role_permissions"]["deployer"]
    assert "approve" not in s["role_permissions"]["deployer"]
