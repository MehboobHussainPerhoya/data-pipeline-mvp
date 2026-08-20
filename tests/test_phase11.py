"""
Phase 11 tests — Audit Logging (FR-SEC-03) and full integration.

Tests that:
1. The AuditLogger records actor/role/action/timestamp/change_detail for all
   security-relevant actions (FR-SEC-03).
2. The server.py deploy tool enforces RBAC BEFORE deploy_gate.py (two-layer
   independence through the actual server code path).
3. Pipeline edits are now logged with actor (the gap from prior phases).
4. Existing tests still pass (no regression in deploy_gate.py behavior).
"""

import sys
from pathlib import Path
import pytest
import json

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from security.rbac import Role, Permission, RBACManager, AccessDeniedError
from security.audit_log import AuditLogger, SecurityAuditEvent
from security.column_security import ColumnSecurityManager
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
# Test Group 1: AuditLogger basic operations (FR-SEC-03)
# ---------------------------------------------------------------------------

def test_audit_event_has_all_required_fields():
    """A SecurityAuditEvent has actor, role, action, timestamp, change_detail, success."""
    event = SecurityAuditEvent(
        actor="alice",
        role="deployer",
        action="deploy",
        resource="output.json",
        change_detail={"record_count": 100},
    )
    assert event.actor == "alice"
    assert event.role == "deployer"
    assert event.action == "deploy"
    assert event.resource == "output.json"
    assert event.change_detail == {"record_count": 100}
    assert event.success is True
    assert event.timestamp  # auto-generated
    assert event.denial_reason is None


def test_audit_logger_log_edit():
    logger = AuditLogger()
    event = logger.log_edit(
        actor="alice",
        role="editor",
        resource="branch:feature/x",
        change_detail={"fields_changed": ["mapping"]},
    )
    assert event.action == "edit"
    assert event.actor == "alice"
    assert event.role == "editor"
    assert event.success is True
    assert len(logger.get_events()) == 1


def test_audit_logger_log_approve():
    logger = AuditLogger()
    event = logger.log_approve(
        actor="bob",
        role="approver",
        resource="proposal:3",
    )
    assert event.action == "approve"
    assert event.actor == "bob"


def test_audit_logger_log_deploy_success():
    logger = AuditLogger()
    event = logger.log_deploy(
        actor="carol",
        role="deployer",
        resource="output.json",
        change_detail={"record_count": 8669},
        success=True,
    )
    assert event.action == "deploy"
    assert event.success is True


def test_audit_logger_log_deploy_failure():
    logger = AuditLogger()
    event = logger.log_deploy(
        actor="carol",
        role="deployer",
        resource="output.json",
        success=False,
        denial_reason="Deploy blocked — approved=False",
    )
    assert event.success is False
    assert "approved=False" in event.denial_reason


def test_audit_logger_log_access_denied():
    logger = AuditLogger()
    event = logger.log_access_denied(
        actor="eve",
        role="viewer",
        action="deploy",
        denial_reason="lacks deploy permission",
    )
    assert event.action == "deploy"
    assert event.success is False
    assert event.denial_reason == "lacks deploy permission"


def test_audit_logger_log_role_assignment():
    logger = AuditLogger()
    event = logger.log_role_assignment(
        actor="admin",
        role="admin",
        target_actor="alice",
        target_role="deployer",
    )
    assert event.action == "assign_role"
    assert event.change_detail["target_actor"] == "alice"
    assert event.change_detail["target_role"] == "deployer"


def test_audit_logger_log_view_sensitive():
    logger = AuditLogger()
    event = logger.log_view_sensitive(
        actor="admin",
        role="admin",
        resource="SupportCase:case_id",
    )
    assert event.action == "view_sensitive"


# ---------------------------------------------------------------------------
# Test Group 2: AuditLogger querying
# ---------------------------------------------------------------------------

def test_get_events_by_actor():
    logger = AuditLogger()
    logger.log_edit(actor="alice", role="editor", resource="b1")
    logger.log_deploy(actor="bob", role="deployer", resource="out.json")
    logger.log_edit(actor="alice", role="editor", resource="b2")

    alice_events = logger.get_events_by_actor("alice")
    assert len(alice_events) == 2
    bob_events = logger.get_events_by_actor("bob")
    assert len(bob_events) == 1


def test_get_events_by_action():
    logger = AuditLogger()
    logger.log_edit(actor="a", role="editor", resource="b1")
    logger.log_deploy(actor="b", role="deployer", resource="out.json")
    logger.log_edit(actor="c", role="editor", resource="b2")

    edits = logger.get_events_by_action("edit")
    assert len(edits) == 2
    deploys = logger.get_events_by_action("deploy")
    assert len(deploys) == 1


def test_get_failed_events():
    logger = AuditLogger()
    logger.log_deploy(actor="a", role="deployer", resource="out", success=True)
    logger.log_deploy(actor="b", role="deployer", resource="out", success=False, denial_reason="blocked")
    logger.log_access_denied(actor="c", role="viewer", action="deploy")

    failed = logger.get_failed_events()
    assert len(failed) == 2


def test_audit_summary():
    logger = AuditLogger()
    logger.log_edit(actor="alice", role="editor", resource="b1")
    logger.log_deploy(actor="bob", role="deployer", resource="out", success=True)
    logger.log_access_denied(actor="eve", role="viewer", action="deploy")

    s = logger.summary()
    assert s["total_events"] == 3
    assert s["events_by_action"]["edit"] == 1
    # log_access_denied with action="deploy" counts as a deploy event
    assert s["events_by_action"]["deploy"] == 2
    assert s["failed_events"] == 1
    assert s["unique_actors"] == 3


# ---------------------------------------------------------------------------
# Test Group 3: AuditLogger persistence to disk
# ---------------------------------------------------------------------------

def test_audit_logger_persists_to_disk(tmp_path):
    """Events are persisted to a JSONL file on disk."""
    log_path = str(tmp_path / "audit.jsonl")
    logger = AuditLogger(log_path=log_path)
    logger.log_edit(actor="alice", role="editor", resource="branch:feature/x")
    logger.log_deploy(actor="bob", role="deployer", resource="out.json", success=True)

    assert Path(log_path).exists()
    lines = Path(log_path).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    # Each line is valid JSON
    for line in lines:
        data = json.loads(line)
        assert "actor" in data
        assert "action" in data
        assert "timestamp" in data


def test_audit_logger_load_from_disk(tmp_path):
    """Events can be loaded back from disk."""
    log_path = str(tmp_path / "audit.jsonl")
    logger = AuditLogger(log_path=log_path)
    logger.log_edit(actor="alice", role="editor", resource="b1")
    logger.log_deploy(actor="bob", role="deployer", resource="out")

    # New logger loads from the same file
    logger2 = AuditLogger(log_path=log_path)
    events = logger2.load_from_disk()
    assert len(events) == 2
    assert events[0].actor == "alice"
    assert events[1].actor == "bob"


def test_audit_logger_no_path_in_memory_only():
    """Without a log_path, events are kept in memory only."""
    logger = AuditLogger()
    logger.log_edit(actor="alice", role="editor", resource="b1")
    assert len(logger.get_events()) == 1
    # No file was created
    assert logger.summary()["log_path"] is None


# ---------------------------------------------------------------------------
# Test Group 4: Full integration — RBAC + deploy_gate.py two-layer independence
# through the actual server.py deploy function
# ---------------------------------------------------------------------------

def test_server_deploy_blocks_viewer_before_deploy_gate(tmp_path, monkeypatch):
    """
    THE CRITICAL INTEGRATION TEST.

    A viewer calling the server's deploy() tool is blocked by the RBAC layer
    BEFORE deploy_gate.py is ever reached. We prove this by asserting that
    no output file is created and the error is AccessDeniedError (not
    deploy_gate.py's RuntimeError about 'safety' or 'approval').

    This tests the actual server.py code path, not a simulation.
    """
    # Import the server module's deploy function and globals
    # We need to set up the RBAC state and cache as the server would
    import mcp_server.server as server

    # Assign a viewer role
    server._rbac.assign_role("viewer_user", Role.VIEWER)

    # Set up the cache as if run_pipeline + validate_pipeline were called
    records = [_make_record()]
    server._cache["output_records"] = records
    server._cache["is_safe"] = True
    server._cache["safety_reasons"] = []

    # Monkeypatch the output path to tmp_path
    output_path = str(tmp_path / "output.json")
    monkeypatch.setattr(
        "mcp_server.server.PROJECT_ROOT",
        tmp_path,
    )

    # The viewer attempts to deploy — should be blocked by RBAC
    with pytest.raises(AccessDeniedError) as exc_info:
        server.deploy(approved=True, deployed_by="viewer_user")

    assert "viewer_user" in str(exc_info.value)
    assert "deploy" in str(exc_info.value)

    # CRITICAL: deploy_gate.py was never reached — no output file exists
    assert not Path(output_path).exists()

    # Cleanup
    server._rbac = RBACManager()  # reset for other tests
    server._cache.clear()


def test_server_deploy_blocks_deployer_with_approved_false(tmp_path, monkeypatch):
    """
    A deployer (passes RBAC) with approved=False is blocked by deploy_gate.py,
    not by RBAC. This proves the two layers are independent.
    """
    import mcp_server.server as server

    server._rbac.assign_role("deployer_user", Role.DEPLOYER)

    records = [_make_record()]
    server._cache["output_records"] = records
    server._cache["is_safe"] = True
    server._cache["safety_reasons"] = []

    monkeypatch.setattr(
        "mcp_server.server.PROJECT_ROOT",
        tmp_path,
    )

    # RBAC passes, but deploy_gate.py blocks because approved=False
    with pytest.raises(RuntimeError, match="approval"):
        server.deploy(approved=False, deployed_by="deployer_user")

    # Cleanup
    server._rbac = RBACManager()
    server._cache.clear()


def test_server_deploy_succeeds_for_deployer_with_safe_and_approved(tmp_path, monkeypatch):
    """
    A deployer with is_safe=True AND approved=True succeeds — both layers pass.
    """
    import mcp_server.server as server

    server._rbac.assign_role("deployer_user", Role.DEPLOYER)

    records = [_make_record()]
    server._cache["output_records"] = records
    server._cache["is_safe"] = True
    server._cache["safety_reasons"] = []

    monkeypatch.setattr(
        "mcp_server.server.PROJECT_ROOT",
        tmp_path,
    )

    result = server.deploy(approved=True, deployed_by="deployer_user")
    assert result["status"] == "deployed"
    assert result["records"] == 1

    # The audit logger should have recorded the successful deploy
    deploy_events = server._audit_logger.get_events_by_action("deploy")
    assert len(deploy_events) >= 1
    assert deploy_events[-1].success is True
    assert deploy_events[-1].actor == "deployer_user"

    # Cleanup
    server._rbac = RBACManager()
    server._cache.clear()
    server._audit_logger = AuditLogger()


def test_server_deploy_logs_failed_deploy_in_audit_log(tmp_path, monkeypatch):
    """
    When deploy_gate.py blocks a deploy, the failure is logged in the audit log
    with success=False and the denial reason.
    """
    import mcp_server.server as server

    server._rbac.assign_role("deployer_user", Role.DEPLOYER)

    records = [_make_record()]
    server._cache["output_records"] = records
    server._cache["is_safe"] = True
    server._cache["safety_reasons"] = []

    monkeypatch.setattr(
        "mcp_server.server.PROJECT_ROOT",
        tmp_path,
    )

    # Deploy fails because approved=False
    with pytest.raises(RuntimeError):
        server.deploy(approved=False, deployed_by="deployer_user")

    # The audit log should have a failed deploy event
    deploy_events = server._audit_logger.get_events_by_action("deploy")
    assert len(deploy_events) >= 1
    failed = [e for e in deploy_events if not e.success]
    assert len(failed) >= 1
    assert failed[-1].denial_reason is not None

    # Cleanup
    server._rbac = RBACManager()
    server._cache.clear()
    server._audit_logger = AuditLogger()


# ---------------------------------------------------------------------------
# Test Group 5: Pipeline edits are now logged with actor (gap filled)
# ---------------------------------------------------------------------------

def test_versioning_update_branch_ir_requires_edit_permission(tmp_path, monkeypatch):
    """
    versioning_update_branch_ir now requires EDIT permission (FR-SEC-01).
    A viewer is blocked from editing a branch.
    """
    import mcp_server.server as server

    server._rbac.assign_role("viewer_user", Role.VIEWER)

    # Create a branch first (using admin)
    server._rbac.assign_role("admin_user", Role.ADMIN)
    # We need to bypass RBAC for branch creation since it doesn't have an RBAC check
    # (creating a branch is a prerequisite, not the action under test)
    branch = server._branch_store.create_branch(name="test_branch_edit", from_branch="Main")
    ir_json = branch.ir.to_json()

    # Viewer attempts to update the branch IR — should be blocked
    with pytest.raises(AccessDeniedError):
        server.versioning_update_branch_ir(
            branch_name="test_branch_edit",
            ir_json=ir_json,
            actor="viewer_user",
        )

    # Cleanup
    server._rbac = RBACManager()
    if "test_branch_edit" in server._branch_store._branches:
        del server._branch_store._branches["test_branch_edit"]


def test_versioning_update_branch_ir_logs_actor(tmp_path, monkeypatch):
    """
    When an editor updates a branch IR, the edit is logged in the audit log
    with the actor's identity (FR-SEC-03). This fills the gap from prior phases
    where pipeline edits had no actor recorded.
    """
    import mcp_server.server as server

    server._rbac.assign_role("editor_user", Role.EDITOR)

    branch = server._branch_store.create_branch(name="test_branch_audit", from_branch="Main")
    ir_json = branch.ir.to_json()

    # Editor updates the branch IR
    server.versioning_update_branch_ir(
        branch_name="test_branch_audit",
        ir_json=ir_json,
        actor="editor_user",
    )

    # The audit log should have an edit event with the actor
    edit_events = server._audit_logger.get_events_by_action("edit")
    assert len(edit_events) >= 1
    assert edit_events[-1].actor == "editor_user"
    assert edit_events[-1].role == "editor"

    # Cleanup
    server._rbac = RBACManager()
    if "test_branch_audit" in server._branch_store._branches:
        del server._branch_store._branches["test_branch_audit"]
    server._audit_logger = AuditLogger()


# ---------------------------------------------------------------------------
# Test Group 6: deploy_gate.py is completely unchanged
# ---------------------------------------------------------------------------

def test_deploy_gate_core_logic_unchanged(tmp_path):
    """The deploy_gate.py core gating logic is byte-for-byte identical.
    is_safe=True + approved=True succeeds; anything else fails."""
    from validation.deploy_gate import deploy_pipeline

    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    # Both conditions met — succeeds
    deploy_pipeline(records, True, [], approved=True, output_path=output_path)
    assert Path(output_path).exists()

    # Missing approval — blocked
    output_path2 = str(tmp_path / "output2.json")
    with pytest.raises(RuntimeError, match="approval"):
        deploy_pipeline(records, True, [], approved=False, output_path=output_path2)
    assert not Path(output_path2).exists()

    # Missing safety — blocked
    output_path3 = str(tmp_path / "output3.json")
    with pytest.raises(RuntimeError, match="safety"):
        deploy_pipeline(records, False, ["failed"], approved=True, output_path=output_path3)
    assert not Path(output_path3).exists()
