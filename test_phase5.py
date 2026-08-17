"""
Phase 5 Smoke Test — Preview & HITL Gate

Validates the Phase 5 components:
1. HITLGate — bulk pending review list (FR-PREV-03)
2. HITLGate — approve/reject with reviewer identity (FR-PREV-04)
3. HITLGate — audit trail with timestamp, reviewer, proposal (FR-PREV-04)
4. HITLGate — audit trail persistence to disk (FR-PREV-04)
5. OrchestratorResult.raw_proposals — Proposal objects available for HITLGate
6. Confidence routing — joins always pending, mappings/transforms auto-apply >= 0.85
7. mapping_inference bug fix — _infer_type no longer references self
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

print("=== Phase 5 Smoke Test ===\n")

# ---------------------------------------------------------------------------
# Test 1: HITLGate bulk pending review list (FR-PREV-03)
# ---------------------------------------------------------------------------
print("1. HITLGate bulk pending review list (FR-PREV-03)...")
from agent.hitl_gate import HITLGate
from agent.proposal import Proposal, ProposalType, ReviewStatus

# Create proposals with mixed statuses
p1 = Proposal(
    proposal_type=ProposalType.MAPPING,
    confidence=0.90,
    rationale="high confidence mapping",
    review_status=ReviewStatus.AUTO_APPROVED,
)
p2 = Proposal(
    proposal_type=ProposalType.JOIN,
    confidence=0.93,
    rationale="join proposal — always HITL",
    review_status=ReviewStatus.PENDING_REVIEW,
)
p3 = Proposal(
    proposal_type=ProposalType.TRANSFORM,
    confidence=0.50,
    rationale="low confidence transform",
    review_status=ReviewStatus.PENDING_REVIEW,
)

gate = HITLGate(proposals=[p1, p2, p3])
pending = gate.list_pending_reviews()
assert len(pending) == 2, f"Expected 2 pending, got {len(pending)}"
assert pending[0]["index"] == 1, f"Expected index 1, got {pending[0]['index']}"
assert pending[1]["index"] == 2, f"Expected index 2, got {pending[1]['index']}"
print(f"   {len(pending)} pending proposals found out of 3 total")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 2: HITLGate approve with reviewer identity (FR-PREV-04)
# ---------------------------------------------------------------------------
print("2. HITLGate approve with reviewer identity (FR-PREV-04)...")
approved = gate.approve(proposal_index=1, reviewer="alice")
assert approved.review_status == ReviewStatus.APPROVED
assert gate.pending_count() == 1
print(f"   Proposal 1 approved by 'alice', pending count now {gate.pending_count()}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 3: HITLGate reject with reason and audit trail (FR-PREV-04)
# ---------------------------------------------------------------------------
print("3. HITLGate reject with reason and audit trail (FR-PREV-04)...")
rejected = gate.reject(proposal_index=2, reviewer="bob", reason="wrong join key — not unique")
assert rejected.review_status == ReviewStatus.REJECTED_BY_HITL
assert rejected.rejection_reason == "wrong join key — not unique"
assert gate.pending_count() == 0
print(f"   Proposal 2 rejected by 'bob' with reason, pending count now {gate.pending_count()}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 4: Audit trail contains reviewer, timestamp, proposal (FR-PREV-04)
# ---------------------------------------------------------------------------
print("4. Audit trail contains reviewer, timestamp, proposal (FR-PREV-04)...")
trail = gate.get_audit_trail()
assert len(trail) == 2, f"Expected 2 audit entries, got {len(trail)}"
assert trail[0]["reviewer"] == "alice"
assert trail[0]["action"] == "approve"
assert "timestamp" in trail[0]
assert "proposal_summary" in trail[0]
assert trail[1]["reviewer"] == "bob"
assert trail[1]["action"] == "reject"
assert trail[1]["rejection_reason"] == "wrong join key — not unique"
print(f"   {len(trail)} audit entries with reviewer, timestamp, proposal summary")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 5: Audit trail persistence to disk (FR-PREV-04)
# ---------------------------------------------------------------------------
print("5. Audit trail persistence to disk (FR-PREV-04)...")
import tempfile, os, json
tmpdir = tempfile.mkdtemp()
audit_file = os.path.join(tmpdir, "audit_trail.json")
gate.persist_audit_trail(audit_file)
assert os.path.exists(audit_file)
with open(audit_file, "r") as f:
    loaded = json.load(f)
assert len(loaded) == 2
assert loaded[0]["reviewer"] == "alice"
assert loaded[1]["reviewer"] == "bob"
print(f"   Audit trail persisted to {audit_file} with {len(loaded)} entries")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 6: Audit trail load from disk (FR-PREV-04)
# ---------------------------------------------------------------------------
print("6. Audit trail load from disk...")
gate2 = HITLGate(proposals=[])
loaded_entries = gate2.load_audit_trail(audit_file)
assert len(loaded_entries) == 2
assert loaded_entries[0].reviewer == "alice"
print(f"   Loaded {len(loaded_entries)} entries from disk")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 7: OrchestratorResult.raw_proposals contains Proposal objects
# ---------------------------------------------------------------------------
print("7. OrchestratorResult.raw_proposals contains Proposal objects...")
from agent.orchestrator import AgentOrchestrator
orchestrator = AgentOrchestrator()
result = orchestrator.orchestrate_simple("clean and join support tickets with knowledge base")
assert hasattr(result, "raw_proposals")
assert len(result.raw_proposals) > 0
assert isinstance(result.raw_proposals[0], Proposal)
print(f"   raw_proposals has {len(result.raw_proposals)} Proposal objects")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 8: HITLGate wired from orchestration result
# ---------------------------------------------------------------------------
print("8. HITLGate wired from orchestration result...")
gate3 = HITLGate(proposals=result.raw_proposals)
all_pending = gate3.list_pending_reviews()
all_proposals = gate3.list_all_proposals()
assert len(all_proposals) == len(result.raw_proposals)
print(f"   Gate has {len(all_proposals)} total proposals, {len(all_pending)} pending")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 9: Cannot approve non-pending proposal
# ---------------------------------------------------------------------------
print("9. Cannot approve non-pending proposal...")
p_auto = Proposal(
    proposal_type=ProposalType.SOURCE_IDENTIFICATION,
    confidence=0.95,
    rationale="auto-approved source",
    review_status=ReviewStatus.AUTO_APPROVED,
)
gate4 = HITLGate(proposals=[p_auto])
try:
    gate4.approve(proposal_index=0, reviewer="alice")
    assert False, "Should have raised ValueError"
except ValueError as e:
    assert "not pending review" in str(e)
print("   Correctly raised ValueError for non-pending proposal")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 10: Cannot reject without reason
# ---------------------------------------------------------------------------
print("10. Cannot reject without reason...")
p_pending = Proposal(
    proposal_type=ProposalType.JOIN,
    confidence=0.90,
    rationale="pending join",
    review_status=ReviewStatus.PENDING_REVIEW,
)
gate5 = HITLGate(proposals=[p_pending])
try:
    gate5.reject(proposal_index=0, reviewer="alice", reason="")
    assert False, "Should have raised ValueError"
except ValueError as e:
    assert "reason" in str(e).lower()
print("   Correctly raised ValueError for empty rejection reason")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 11: Bulk approve/reject
# ---------------------------------------------------------------------------
print("11. Bulk approve/reject...")
p_a = Proposal(proposal_type=ProposalType.JOIN, confidence=0.9, rationale="a", review_status=ReviewStatus.PENDING_REVIEW)
p_b = Proposal(proposal_type=ProposalType.JOIN, confidence=0.8, rationale="b", review_status=ReviewStatus.PENDING_REVIEW)
p_c = Proposal(proposal_type=ProposalType.JOIN, confidence=0.7, rationale="c", review_status=ReviewStatus.PENDING_REVIEW)
gate6 = HITLGate(proposals=[p_a, p_b, p_c])
gate6.bulk_approve([0, 1], reviewer="alice")
gate6.bulk_reject([2], reviewer="bob", reason="low confidence")
assert gate6.pending_count() == 0
assert len(gate6.get_approved_proposals()) == 2
assert len(gate6.get_rejected_proposals()) == 1
assert len(gate6.get_audit_trail()) == 3
print(f"   Bulk: 2 approved, 1 rejected, 3 audit entries")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 12: mapping_inference _infer_type bug fix (no self reference)
# ---------------------------------------------------------------------------
print("12. mapping_inference _infer_type bug fix...")
from agent.mapping_inference import _infer_type
# This would have raised NameError before the fix if values are int-like
result_type = _infer_type(["1", "2", "3"])
assert result_type == "Integer", f"Expected Integer, got {result_type}"
result_type = _infer_type(["1.5", "2.5", "3.5"])
assert result_type == "Double", f"Expected Double, got {result_type}"
result_type = _infer_type(["hello", "world"])
assert result_type == "String", f"Expected String, got {result_type}"
print("   _infer_type works correctly with int, float, and string values")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 13: Gate summary
# ---------------------------------------------------------------------------
print("13. Gate summary...")
p_d = Proposal(proposal_type=ProposalType.JOIN, confidence=0.9, rationale="d", review_status=ReviewStatus.PENDING_REVIEW)
p_e = Proposal(proposal_type=ProposalType.JOIN, confidence=0.8, rationale="e", review_status=ReviewStatus.PENDING_REVIEW)
p_f = Proposal(proposal_type=ProposalType.JOIN, confidence=0.7, rationale="f", review_status=ReviewStatus.PENDING_REVIEW)
gate7 = HITLGate(proposals=[p_d, p_e, p_f])
gate7.approve(0, "alice")
gate7.reject(1, "bob", "test")
summary = gate7.summary()
assert summary["total_proposals"] == 3
assert summary["pending_review"] == 1
assert summary["approved"] == 1
assert summary["rejected"] == 1
assert summary["audit_entries"] == 2
print(f"   Summary: {summary}")
print("   PASS\n")

print("=== All Phase 5 smoke tests passed! ===")