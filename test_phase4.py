"""
Phase 4 Smoke Test — AI Agent Orchestrator

Validates the Phase 4 components:
1. Proposal data structure (confidence, rationale, review status)
2. Confidence scoring and FSD-fixed threshold routing
3. Source identification stage
4. Mapping inference stage
5. Join inference stage (with uniqueness detection — the CLV lesson)
6. Transform generation stage
7. Full end-to-end orchestration
8. deploy_gate.py unchanged invariant
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

print("=== Phase 4 Smoke Test ===\n")

# ---------------------------------------------------------------------------
# Test 1: Proposal data structure
# ---------------------------------------------------------------------------
from agent.proposal import Proposal, ProposalType, ReviewStatus

print("1. Creating a Proposal...")
p = Proposal(
    proposal_type=ProposalType.JOIN,
    confidence=0.93,
    rationale="Field product_id exists in both datasets with matching types and 95% value overlap.",
    evidence={"name_match": True, "type_match": True, "value_overlap": 0.95, "uniqueness_ratio": 0.34},
    review_status=ReviewStatus.UNREVIEWED,
)
assert p.confidence == 0.93
assert p.proposal_type == ProposalType.JOIN
assert "product_id" in p.rationale
print(f"   {p.summary()}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 2: Confidence thresholds are FSD-fixed
# ---------------------------------------------------------------------------
from agent.confidence import MAPPING_AUTO_THRESHOLD, TRANSFORM_AUTO_THRESHOLD, JOIN_HITL_THRESHOLD

print("2. FSD-fixed confidence thresholds...")
print(f"   Mapping auto-apply threshold: {MAPPING_AUTO_THRESHOLD}")
print(f"   Transform auto-apply threshold: {TRANSFORM_AUTO_THRESHOLD}")
print(f"   Join HITL threshold: {JOIN_HITL_THRESHOLD}")
assert MAPPING_AUTO_THRESHOLD == 0.85
assert TRANSFORM_AUTO_THRESHOLD == 0.85
assert JOIN_HITL_THRESHOLD == 0.95
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 3: route_proposal — mapping above threshold auto-approves
# ---------------------------------------------------------------------------
from agent.confidence import route_proposal

print("3. Routing mapping proposal above threshold...")
p_high = Proposal(
    proposal_type=ProposalType.MAPPING,
    confidence=0.90,
    rationale="High confidence mapping",
)
p_high = route_proposal(p_high)
assert p_high.review_status == ReviewStatus.AUTO_APPROVED
assert p_high.auto_eligible == True
print(f"   confidence=0.90 -> {p_high.review_status.value}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 4: route_proposal — mapping below threshold goes to HITL
# ---------------------------------------------------------------------------
print("4. Routing mapping proposal below threshold...")
p_low = Proposal(
    proposal_type=ProposalType.MAPPING,
    confidence=0.50,
    rationale="Low confidence mapping",
)
p_low = route_proposal(p_low)
assert p_low.review_status == ReviewStatus.PENDING_REVIEW
assert p_low.auto_eligible == False
print(f"   confidence=0.50 -> {p_low.review_status.value}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 5: route_proposal — join ALWAYS goes to HITL regardless of score
# ---------------------------------------------------------------------------
print("5. Routing join proposal (always HITL in Phase 4)...")
p_join_high = Proposal(
    proposal_type=ProposalType.JOIN,
    confidence=0.99,
    rationale="Very high confidence join",
)
p_join_high = route_proposal(p_join_high)
assert p_join_high.review_status == ReviewStatus.PENDING_REVIEW
assert p_join_high.auto_eligible == False
print(f"   confidence=0.99 -> {p_join_high.review_status.value} (joins NEVER auto-apply in Phase 4)")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 6: Source identification stage
# ---------------------------------------------------------------------------
from agent.source_identifier import SourceIdentifier, get_default_catalog

print("6. Source identification stage...")
identifier = SourceIdentifier(get_default_catalog())
proposals = identifier.identify_sources("clean and join support tickets with knowledge base")
assert len(proposals) > 0
assert all(p.proposal_type == ProposalType.SOURCE_IDENTIFICATION for p in proposals)
assert all(p.review_status == ReviewStatus.AUTO_APPROVED for p in proposals)  # Low risk, auto-proceed
print(f"   Found {len(proposals)} source proposals")
for p in proposals:
    print(f"     {p.evidence.get('source_name')}: confidence={p.confidence}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 7: Mapping inference stage
# ---------------------------------------------------------------------------
from agent.mapping_inference import MappingInference, SourceFieldProfile, CanonicalFieldTarget

print("7. Mapping inference stage...")
inferrer = MappingInference()
source_fields = [
    SourceFieldProfile(name="Ticket Subject", sample_values=["Issue with product", "Refund request"]),
    SourceFieldProfile(name="Ticket Status", sample_values=["Open", "Closed"]),
    SourceFieldProfile(name="Ticket Type", sample_values=["Refund request", "Technical issue"]),
]
canonical_fields = [
    CanonicalFieldTarget(name="subject", canonical_type="String"),
    CanonicalFieldTarget(name="status", canonical_type="String"),
    CanonicalFieldTarget(name="ticket_type", canonical_type="String"),
]
mappings = inferrer.infer_mappings("tickets", source_fields, canonical_fields)
assert len(mappings) > 0
assert all(p.proposal_type == ProposalType.MAPPING for p in mappings)
print(f"   Found {len(mappings)} mapping proposals")
for p in mappings:
    src = p.evidence.get("source_field")
    dst = p.evidence.get("canonical_field")
    print(f"     {src} -> {dst}: confidence={p.confidence}, status={p.review_status.value}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 8: Join inference — GOOD key (unique)
# ---------------------------------------------------------------------------
from agent.join_inference import JoinInference, JoinKeyCandidate

print("8. Join inference with GOOD (unique) key...")
join_inferrer = JoinInference()
# Simulate a unique key: 5 unique values
good_candidate = JoinKeyCandidate(
    left_key="product_variation_id",
    right_key="product_variation_id",
    left_values=["pv1", "pv2", "pv3", "pv4", "pv5"],
    right_values=["pv1", "pv2", "pv3", "pv4", "pv5"],
    left_type="String",
    right_type="String",
)
joins_good = join_inferrer.infer_joins("transactions", "products", [good_candidate])
assert len(joins_good) == 1
good_proposal = joins_good[0]
ur_good = good_proposal.evidence["uniqueness_ratio"]
print(f"   confidence={good_proposal.confidence}, uniqueness_ratio={ur_good}")
print(f"   rationale: {good_proposal.rationale[:120]}...")
assert ur_good == 1.0
assert good_proposal.confidence > 0.5
# Joins always go to HITL regardless
assert good_proposal.review_status == ReviewStatus.PENDING_REVIEW
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 9: Join inference — BAD key (non-unique, the CLV lesson)
# ---------------------------------------------------------------------------
print("9. Join inference with BAD (non-unique) key — the CLV lesson...")
# Simulate a non-unique key: 5 values but only 3 unique (product_id repeats)
bad_candidate = JoinKeyCandidate(
    left_key="product_id",
    right_key="product_id",
    left_values=["p1", "p2", "p3", "p4", "p5"],
    right_values=["p1", "p1", "p1", "p1", "p2"],  # only 2 unique out of 5
    left_type="String",
    right_type="String",
)
joins_bad = join_inferrer.infer_joins("transactions", "products", [bad_candidate])
assert len(joins_bad) == 1
bad_proposal = joins_bad[0]
ur_bad = bad_proposal.evidence["uniqueness_ratio"]
print(f"   confidence={bad_proposal.confidence}, uniqueness_ratio={ur_bad}")
print(f"   rationale: {bad_proposal.rationale[:200]}...")
assert ur_bad < 0.7  # non-unique
assert bad_proposal.confidence < good_proposal.confidence  # lower confidence than good key
assert "NOT unique" in bad_proposal.rationale or "WARNING" in bad_proposal.rationale  # flagged
assert bad_proposal.review_status == ReviewStatus.PENDING_REVIEW
print(f"   Bad key confidence ({bad_proposal.confidence}) < Good key confidence ({good_proposal.confidence})")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 10: Transform generation stage
# ---------------------------------------------------------------------------
from agent.transform_generation import TransformGeneration

print("10. Transform generation stage...")
generator = TransformGeneration()
transforms = generator.generate_transforms(
    request="clean and trim support tickets",
    source_name="tickets",
    fields=[
        {"name": "Ticket Subject", "type": "String", "values": ["  hello  ", "world"]},
        {"name": "Ticket Description", "type": "String", "values": ["desc"]},
    ],
)
assert len(transforms) > 0
assert all(p.proposal_type == ProposalType.TRANSFORM for p in transforms)
print(f"   Found {len(transforms)} transform proposals")
for p in transforms:
    tname = p.evidence.get("transform_name")
    fname = p.evidence.get("field_name")
    print(f"     {tname} on {fname}: confidence={p.confidence}, status={p.review_status.value}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 11: Full orchestration end-to-end
# ---------------------------------------------------------------------------
from agent.orchestrator import AgentOrchestrator

print("11. Full orchestration end-to-end...")
orchestrator = AgentOrchestrator()
result = orchestrator.orchestrate(
    request="clean and join support tickets with knowledge base",
    source_profiles={
        "support_tickets": source_fields,
    },
    canonical_fields=canonical_fields,
    join_candidates=[good_candidate, bad_candidate],
    join_pairs=[("support_tickets", "kb_articles")],
)
total = len(result.all_proposals)
print(f"   Total proposals: {total}")
print(f"   Auto-approved: {result.auto_approved_count}")
print(f"   Pending review: {result.pending_review_count}")
by_type = result.summary()["by_type"]
print(f"   By type: {by_type}")
assert total > 0
assert result.pending_review_count > 0  # joins always pending
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 12: Every proposal has a rationale string (FR-AGENT-06)
# ---------------------------------------------------------------------------
print("12. Every proposal has a rationale string...")
for p in result.all_proposals:
    assert "rationale" in p
    assert isinstance(p["rationale"], str)
    assert len(p["rationale"]) > 0
print(f"   All {total} proposals have non-empty rationale strings")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 13: Every proposal has a numeric confidence score (FR-AGENT-05)
# ---------------------------------------------------------------------------
print("13. Every proposal has a numeric confidence score...")
for p in result.all_proposals:
    assert "confidence" in p
    assert isinstance(p["confidence"], (int, float))
    assert 0.0 <= p["confidence"] <= 1.0
print(f"   All {total} proposals have confidence in [0, 1]")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 14: Join proposals always carry review_status (never auto-apply in Phase 4)
# ---------------------------------------------------------------------------
print("14. Join proposals always carry pending_review status...")
join_props = [p for p in result.all_proposals if p["proposal_type"] == "join"]
for jp in join_props:
    assert jp["review_status"] == "pending_review"
print(f"   All {len(join_props)} join proposals are pending_review (never auto-apply in Phase 4)")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 15: Confidence scoring functions produce sane scores
# ---------------------------------------------------------------------------
from agent.confidence import score_mapping, score_join, score_transform, score_source_identification

print("15. Confidence scoring functions produce sane scores...")
# Perfect mapping
c1, _ = score_mapping(name_similarity=1.0, type_compatible=True, value_overlap=1.0)
assert c1 == 1.0, f"Perfect mapping should be 1.0, got {c1}"
# Terrible mapping
c2, _ = score_mapping(name_similarity=0.0, type_compatible=False, value_overlap=0.0)
assert c2 == 0.0, f"Terrible mapping should be 0.0, got {c2}"
# Perfect join (unique key)
c3, _ = score_join(name_match=True, type_compatible=True, value_overlap=1.0, uniqueness_ratio=1.0)
assert c3 == 1.0, f"Perfect join should be 1.0, got {c3}"
# Terrible join (non-unique key)
c4, _ = score_join(name_match=False, type_compatible=False, value_overlap=0.0, uniqueness_ratio=0.2)
assert c4 < 0.3, f"Terrible join should be < 0.3, got {c4}"
print(f"   Perfect mapping: {c1}, terrible mapping: {c2}")
print(f"   Perfect join: {c3}, terrible join (non-unique): {c4}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 16: deploy_gate.py is unchanged
# ---------------------------------------------------------------------------
print("16. deploy_gate.py unchanged invariant...")
from validation.deploy_gate import deploy_pipeline
from schema.output_schema import JoinedCaseOutput
import tempfile

record = JoinedCaseOutput(
    case_id="t1", subject="test", ticket_type=None,
    matched_category=None, matched_article_count=0, source_system="test",
)
with tempfile.TemporaryDirectory() as tmpdir:
    output_path = str(Path(tmpdir) / "out.json")
    deploy_pipeline([record], True, [], approved=True, output_path=output_path)
    assert Path(output_path).exists()
    print("   Deploy succeeded with safe+approved")

    try:
        deploy_pipeline([record], False, ["unsafe"], approved=True, output_path=output_path)
        assert False, "Should have raised"
    except RuntimeError:
        print("   Deploy blocked on unsafe (correct)")

    try:
        deploy_pipeline([record], True, [], approved=False, output_path=output_path)
        assert False, "Should have raised"
    except RuntimeError:
        print("   Deploy blocked on unapproved (correct)")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 17: Real example — product_id vs product_variation_id join proposal
# This demonstrates the exact CLV pipeline lesson from FSD Section 7
# ---------------------------------------------------------------------------
print("17. Real example: product_id vs product_variation_id (CLV lesson)...")
# product_id: non-unique in products (each product has multiple variations)
product_id_candidate = JoinKeyCandidate(
    left_key="product_id",
    right_key="product_id",
    left_values=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
    right_values=["1", "1", "1", "2", "2", "3", "3", "3", "3", "4"],  # 4 unique out of 10
    left_type="String",
    right_type="String",
)
# product_variation_id: unique in products
product_var_candidate = JoinKeyCandidate(
    left_key="product_id",
    right_key="product_variation_id",
    left_values=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
    right_values=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],  # all unique
    left_type="String",
    right_type="String",
)
clv_joins = join_inferrer.infer_joins("transactions", "products", [product_id_candidate, product_var_candidate])
assert len(clv_joins) == 2
# Sort by confidence to find the winner
clv_joins.sort(key=lambda p: p.confidence, reverse=True)
winner = clv_joins[0]
loser = clv_joins[1]
print(f"   Winner: {winner.evidence['right_key']} (confidence={winner.confidence}, uniqueness={winner.evidence['uniqueness_ratio']})")
print(f"   Loser:  {loser.evidence['right_key']} (confidence={loser.confidence}, uniqueness={loser.evidence['uniqueness_ratio']})")
assert winner.evidence["right_key"] == "product_variation_id"
assert loser.evidence["right_key"] == "product_id"
assert winner.confidence > loser.confidence
assert "NOT unique" in loser.rationale or "WARNING" in loser.rationale
print(f"   The orchestrator correctly ranks product_variation_id above product_id")
print(f"   and flags product_id as non-unique — exactly the CLV lesson from FSD Section 7.")
print("   PASS\n")

print("=== All Phase 4 smoke tests passed! ===")