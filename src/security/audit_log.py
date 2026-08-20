"""
Full Audit Logging — FR-SEC-03.

All edits, approvals, and deployments logged with actor, timestamp, and
change detail.

Design principle (from phase instructions):
  This does NOT duplicate Phase 6 (lineage), Phase 8 (proposal audit trail),
  or Phase 9/10 (deploy audit log). It ties into / wraps them with actor/role
  context where missing, and fills gaps where an existing log doesn't capture
  the actor.

Existing audit systems and their actor coverage:
  - Phase 5 HITL (hitl_gate.py AuditEntry): captures reviewer + timestamp + action  ✓
  - Phase 8 proposal (proposal.py ReviewRecord): captures reviewer + timestamp + action  ✓
  - Phase 9 deploy (deploy_gate.py audit log): captures deployed_by + timestamp  ✓
  - Pipeline edits (versioning_update_branch_ir): NO actor logged  ✗ ← gap

This module fills the gap by providing a unified AuditLogger that:
  1. Records a SecurityAuditEvent for every security-relevant action
     (edit, approve, deploy, view-sensitive, role-assignment, access-denied).
  2. Each event has: actor, role, action, timestamp, change_detail, success/failure.
  3. Persists to a single audit log file (data/processed/security_audit_log.jsonl).
  4. Can query events by actor, action, or time range.

The existing logs (HITL audit trail, deploy audit log, proposal review records)
continue to exist and serve their specific purposes. This module provides the
unified cross-cutting view that FR-SEC-03 requires — "all edits, approvals,
and deployments logged with actor, timestamp, and change detail."

FSD requirements: FR-SEC-03 (Full audit logging, M)
"""

from datetime import datetime, timezone
from pathlib import Path
import json
from pydantic import BaseModel, Field
from typing import Optional, Any


class SecurityAuditEvent(BaseModel):
    """
    One security-relevant audit event.

    Attributes:
        timestamp: ISO-8601 UTC timestamp
        actor: who performed the action (string identity, reusing Phase 8 pattern)
        role: the actor's role at time of action
        action: what was attempted (edit, approve, deploy, view, etc.)
        resource: what was acted upon (e.g., "branch:feature/x", "proposal:3")
        change_detail: structured details of the change
        success: whether the action succeeded (False for access-denied, failed deploys, etc.)
        denial_reason: if success=False and the cause was an access denial, the reason
    """
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    actor: str
    role: str = "unknown"
    action: str
    resource: str = ""
    change_detail: dict = Field(default_factory=dict)
    success: bool = True
    denial_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return self.model_dump()

    def to_jsonl(self) -> str:
        """Serialize as a single JSON-lines record (for append-only log)."""
        return json.dumps(self.model_dump(), default=str)


class AuditLogger:
    """
    Unified audit logger for security-relevant actions (FR-SEC-03).

    Records SecurityAuditEvent objects and persists them to a JSONL file
    (one JSON object per line — append-only, easy to grep/query).

    This wraps the existing audit systems with a unified actor/role view:
    - Edits (versioning_update_branch_ir) — gap filled, now logged with actor
    - Approvals (HITL + proposal review) — existing logs have reviewer; this
      adds a parallel unified entry with role context
    - Deployments (deploy_gate.py audit log) — existing log has deployed_by;
      this adds a parallel unified entry with role + success/failure context
    - Access denials (RBAC blocks) — new, logged here for security audit

    Usage:
        logger = AuditLogger()
        logger.log_edit(actor="alice", role="editor", resource="branch:feature/x",
                        change_detail={"fields_changed": ["mapping"]})
        logger.log_deploy(actor="bob", role="deployer", resource="output.json",
                          change_detail={"record_count": 8669}, success=True)
        logger.log_access_denied(actor="eve", role="viewer",
                                 action="deploy", denial_reason="lacks deploy permission")
    """

    def __init__(self, log_path: str | None = None):
        """
        Args:
            log_path: path to the JSONL audit log file. If None, events are
                      kept in memory only (useful for tests).
        """
        self._log_path = log_path
        self._events: list[SecurityAuditEvent] = []

    def _record(self, event: SecurityAuditEvent) -> SecurityAuditEvent:
        """Record an event: store in memory and persist to disk if path is set."""
        self._events.append(event)
        if self._log_path:
            p = Path(self._log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(event.to_jsonl() + "\n")
        return event

    # --- Specific action loggers ---

    def log_edit(
        self,
        actor: str,
        role: str,
        resource: str,
        change_detail: dict | None = None,
        success: bool = True,
    ) -> SecurityAuditEvent:
        """Log a pipeline edit action (e.g., updating a branch's IR)."""
        return self._record(SecurityAuditEvent(
            actor=actor,
            role=role,
            action="edit",
            resource=resource,
            change_detail=change_detail or {},
            success=success,
        ))

    def log_approve(
        self,
        actor: str,
        role: str,
        resource: str,
        change_detail: dict | None = None,
        success: bool = True,
    ) -> SecurityAuditEvent:
        """Log an approval action (HITL proposal or version proposal review)."""
        return self._record(SecurityAuditEvent(
            actor=actor,
            role=role,
            action="approve",
            resource=resource,
            change_detail=change_detail or {},
            success=success,
        ))

    def log_deploy(
        self,
        actor: str,
        role: str,
        resource: str,
        change_detail: dict | None = None,
        success: bool = True,
        denial_reason: str | None = None,
    ) -> SecurityAuditEvent:
        """Log a deployment action."""
        return self._record(SecurityAuditEvent(
            actor=actor,
            role=role,
            action="deploy",
            resource=resource,
            change_detail=change_detail or {},
            success=success,
            denial_reason=denial_reason,
        ))

    def log_access_denied(
        self,
        actor: str,
        role: str,
        action: str,
        resource: str = "",
        denial_reason: str = "",
    ) -> SecurityAuditEvent:
        """Log an access denial (RBAC blocked an action)."""
        return self._record(SecurityAuditEvent(
            actor=actor,
            role=role,
            action=action,
            resource=resource,
            success=False,
            denial_reason=denial_reason,
        ))

    def log_role_assignment(
        self,
        actor: str,
        role: str,
        target_actor: str,
        target_role: str,
    ) -> SecurityAuditEvent:
        """Log a role assignment (admin action)."""
        return self._record(SecurityAuditEvent(
            actor=actor,
            role=role,
            action="assign_role",
            resource=f"actor:{target_actor}",
            change_detail={"target_actor": target_actor, "target_role": target_role},
        ))

    def log_view_sensitive(
        self,
        actor: str,
        role: str,
        resource: str,
        change_detail: dict | None = None,
    ) -> SecurityAuditEvent:
        """Log when a role views sensitive column data (for audit compliance)."""
        return self._record(SecurityAuditEvent(
            actor=actor,
            role=role,
            action="view_sensitive",
            resource=resource,
            change_detail=change_detail or {},
        ))

    # --- Querying ---

    def get_events(self) -> list[SecurityAuditEvent]:
        """Return all events in memory."""
        return list(self._events)

    def get_events_by_actor(self, actor: str) -> list[SecurityAuditEvent]:
        """Return all events for a specific actor."""
        return [e for e in self._events if e.actor == actor]

    def get_events_by_action(self, action: str) -> list[SecurityAuditEvent]:
        """Return all events for a specific action type."""
        return [e for e in self._events if e.action == action]

    def get_failed_events(self) -> list[SecurityAuditEvent]:
        """Return all failed events (access denials, failed deploys, etc.)."""
        return [e for e in self._events if not e.success]

    def load_from_disk(self) -> list[SecurityAuditEvent]:
        """Load all events from the JSONL log file into memory."""
        if not self._log_path:
            return []
        p = Path(self._log_path)
        if not p.exists():
            return []
        events = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    events.append(SecurityAuditEvent(**data))
        self._events = events
        return events

    def summary(self) -> dict:
        """Return a summary of the audit log state."""
        actions = {}
        for e in self._events:
            actions[e.action] = actions.get(e.action, 0) + 1
        return {
            "total_events": len(self._events),
            "events_by_action": actions,
            "failed_events": len(self.get_failed_events()),
            "unique_actors": len(set(e.actor for e in self._events)),
            "log_path": self._log_path,
        }
