"""
HITL Gate — Human-In-The-Loop review gate for agent proposals.

This is the proposal-level gate between agent-generated logic and execution.
It manages:
- Collecting all pending-review proposals (FR-PREV-03 bulk review)
- Approving/rejecting proposals with reviewer identity (FR-PREV-04 audit trail)
- Recording an audit trail of every approval/rejection (FR-PREV-04)

The gate does NOT execute anything — it only tracks review state.
Execution happens after proposals are approved.

FSD requirements:
- FR-PREV-02 (confidence-threshold gating — routing is in confidence.py,
  this gate handles the human side of proposals already routed to PENDING_REVIEW)
- FR-PREV-03 (bulk review UI — consolidated pending list with approve/reject)
- FR-PREV-04 (audit trail of approvals — reviewer, timestamp, proposal)
"""

from datetime import datetime, timezone
from pathlib import Path
import json
from pydantic import BaseModel, Field
from typing import Optional, Any
from .proposal import Proposal, ProposalType, ReviewStatus


class AuditEntry(BaseModel):
    """
    One entry in the HITL audit trail.

    FSD FR-PREV-04: Every HITL approval/rejection shall be logged with
    reviewer identity, timestamp, and the reviewed proposal.
    """
    timestamp: str
    reviewer: str
    action: str               # "approve" or "reject"
    proposal_type: str        # ProposalType value
    proposal_summary: dict    # the proposal's summary() at time of review
    rejection_reason: Optional[str] = None

    def __str__(self) -> str:
        base = (
            f"[{self.timestamp}] {self.reviewer} {self.action}ed "
            f"{self.proposal_type} proposal (confidence={self.proposal_summary.get('confidence')})"
        )
        if self.rejection_reason:
            base += f" — reason: {self.rejection_reason}"
        return base


class HITLGate:
    """
    The Human-In-The-Loop review gate.

    Holds a list of proposals (typically the output of an orchestration run)
    and provides bulk review operations: list pending, approve, reject.

    Every approve/reject is recorded in an audit trail (FR-PREV-04).

    Usage:
        gate = HITLGate(proposals=orchestrator_result.all_proposals)
        pending = gate.list_pending_reviews()       # FR-PREV-03
        gate.approve(proposal_index=0, reviewer="alice")  # FR-PREV-04
        gate.reject(proposal_index=1, reviewer="alice", reason="wrong join key")
        audit = gate.get_audit_trail()               # FR-PREV-04
        approved = gate.get_approved_proposals()     # for IR execution
    """

    def __init__(self, proposals: list[Proposal] = None):
        self._proposals: list[Proposal] = proposals or []
        self._audit_trail: list[AuditEntry] = []

    # --- FR-PREV-03: Bulk review list ---

    def list_pending_reviews(self) -> list[dict]:
        """
        Return all proposals currently pending human review.
        This is the consolidated bulk review list (FR-PREV-03).
        """
        return [
            {"index": i, **p.summary()}
            for i, p in enumerate(self._proposals)
            if p.review_status == ReviewStatus.PENDING_REVIEW
        ]

    def list_all_proposals(self) -> list[dict]:
        """Return all proposals with their indices and summaries."""
        return [{"index": i, **p.summary()} for i, p in enumerate(self._proposals)]

    def pending_count(self) -> int:
        """How many proposals are still pending review."""
        return sum(1 for p in self._proposals if p.review_status == ReviewStatus.PENDING_REVIEW)

    # --- FR-PREV-04: Approve / Reject with audit trail ---

    def approve(self, proposal_index: int, reviewer: str) -> Proposal:
        """
        Approve a pending proposal.

        Records an audit entry with reviewer identity and timestamp (FR-PREV-04).
        Returns the approved proposal.
        """
        proposal = self._get_proposal(proposal_index)
        self._assert_pending(proposal, proposal_index)

        proposal.review_status = ReviewStatus.APPROVED
        proposal.auto_eligible = True

        self._audit_trail.append(AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer=reviewer,
            action="approve",
            proposal_type=proposal.proposal_type.value,
            proposal_summary=proposal.summary(),
        ))
        return proposal

    def reject(self, proposal_index: int, reviewer: str, reason: str) -> Proposal:
        """
        Reject a pending proposal. A reason is required.

        Records an audit entry with reviewer identity, timestamp, and reason (FR-PREV-04).
        Returns the rejected proposal.
        """
        if not reason:
            raise ValueError("A reason must be provided when rejecting a proposal.")

        proposal = self._get_proposal(proposal_index)
        self._assert_pending(proposal, proposal_index)

        proposal.review_status = ReviewStatus.REJECTED_BY_HITL
        proposal.rejection_reason = reason
        proposal.auto_eligible = False

        self._audit_trail.append(AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer=reviewer,
            action="reject",
            proposal_type=proposal.proposal_type.value,
            proposal_summary=proposal.summary(),
            rejection_reason=reason,
        ))
        return proposal

    def bulk_approve(self, indices: list[int], reviewer: str) -> list[Proposal]:
        """Approve multiple pending proposals at once."""
        return [self.approve(i, reviewer) for i in indices]

    def bulk_reject(self, indices: list[int], reviewer: str, reason: str) -> list[Proposal]:
        """Reject multiple pending proposals at once with the same reason."""
        return [self.reject(i, reviewer, reason) for i in indices]

    # --- Audit trail (FR-PREV-04) ---

    def get_audit_trail(self) -> list[dict]:
        """Return the full audit trail as a list of dicts."""
        return [entry.model_dump() for entry in self._audit_trail]

    def get_audit_trail_text(self) -> str:
        """Return the audit trail as human-readable text."""
        if not self._audit_trail:
            return "No HITL actions recorded."
        return "\n".join(str(e) for e in self._audit_trail)

    def persist_audit_trail(self, path: str) -> None:
        """
        Write the audit trail to a JSON file on disk (FR-PREV-04).

        Every HITL approval/rejection is persisted with reviewer identity,
        timestamp, and the reviewed proposal. This makes the audit trail
        reconstructable from logs, satisfying the FSD auditability NFR.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump([entry.model_dump() for entry in self._audit_trail], f, indent=2)

    def load_audit_trail(self, path: str) -> list[AuditEntry]:
        """Load a previously persisted audit trail from disk."""
        p = Path(path)
        if not p.exists():
            return []
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = [AuditEntry(**d) for d in data]
        self._audit_trail.extend(entries)
        return entries

    # --- Querying post-review state ---

    def get_approved_proposals(self) -> list[Proposal]:
        """
        Return all proposals that are approved (either by human or auto-approved).
        These are the proposals whose IR operators can proceed to execution.
        """
        return [
            p for p in self._proposals
            if p.review_status in (ReviewStatus.APPROVED, ReviewStatus.AUTO_APPROVED)
        ]

    def get_rejected_proposals(self) -> list[Proposal]:
        """Return all proposals that were rejected by HITL."""
        return [p for p in self._proposals if p.review_status == ReviewStatus.REJECTED_BY_HITL]

    def all_reviews_complete(self) -> bool:
        """True if no proposals are left pending review."""
        return self.pending_count() == 0

    def summary(self) -> dict:
        """Return a summary of the gate's state."""
        return {
            "total_proposals": len(self._proposals),
            "pending_review": self.pending_count(),
            "approved": len(self.get_approved_proposals()),
            "rejected": len(self.get_rejected_proposals()),
            "audit_entries": len(self._audit_trail),
        }

    # --- Internal helpers ---

    def _get_proposal(self, index: int) -> Proposal:
        if index < 0 or index >= len(self._proposals):
            raise ValueError(f"Invalid proposal_index {index}. Range: 0-{len(self._proposals)-1}")
        return self._proposals[index]

    def _assert_pending(self, proposal: Proposal, index: int) -> None:
        if proposal.review_status != ReviewStatus.PENDING_REVIEW:
            raise ValueError(
                f"Proposal at index {index} is not pending review "
                f"(current status: {proposal.review_status.value}). "
                f"Only pending_review proposals can be approved or rejected."
            )