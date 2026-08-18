"""
Phase 6 smoke tests — Provenance & Lineage Tracking (FSD 4.11).

Tests:
1. A Cast operation records correct field-level lineage (FR-PROV-01)
2. A Map operation records correct field-level lineage (FR-PROV-01)
3. A Join records the join rule (FR-PROV-02)
4. A Join from an agent Proposal records confidence score and review status (FR-PROV-02)
5. The query interface traces a real output field back to its source without re-execution (FR-PROV-03)
6. The query interface provides a human-readable explanation (FR-PROV-03)
7. Join provenance correctly distinguishes manual vs agent_inferred joins
8. LineageStore summary reports correct counts
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from provenance.lineage_store import LineageStore, LineageRecord
from provenance.lineage_tracker import LineageTracker
from provenance.query import LineageQuery
from ir.operators import (
    CastOperator, MapOperator, JoinOperator, JoinKeyPair,
    AggregateOperator, AggregationSpec, UnionOperator,
)
from agent.proposal import Proposal, ProposalType, ReviewStatus
from agent.join_inference import JoinInference, JoinKeyCandidate


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        raise AssertionError(f"{name} failed: {detail}")


print("Phase 6 smoke tests — Provenance & Lineage Tracking")
print("=" * 60)

# ---------------------------------------------------------------------------
# Test 1: Cast operation records field-level lineage (FR-PROV-01)
# ---------------------------------------------------------------------------
print("\nTest 1: Cast records field-level lineage")
tracker1 = LineageTracker()
cast_op = CastOperator(
    op="Cast", input="products", field="price", to="Double", output="products_cast",
    source="manual",
)
tracker1.record_execution(
    step=cast_op, step_index=0,
    datasets_before={"products": [{"price": "10.5", "name": "widget"}]},
    output_data=[{"price": 10.5, "name": "widget"}],
)
records = tracker1.store.get_all()
test("Cast produces 1 lineage record", len(records) == 1, f"got {len(records)}")
test("Record operator_type is Cast", records[0].operator_type == "Cast")
test("Record output_field is price", records[0].output_field == "price")
test("Record input_fields is [price]", records[0].input_fields == ["price"])
test("Record operator_params has to=Double", records[0].operator_params.get("to") == "Double")

# ---------------------------------------------------------------------------
# Test 2: Map operation records field-level lineage (FR-PROV-01)
# ---------------------------------------------------------------------------
print("\nTest 2: Map records field-level lineage")
tracker2 = LineageTracker()
map_op = MapOperator(
    op="Map", input="transactions", output="transactions_mapped",
    transform="trim_whitespace", params={"field": "product_name"},
    source="manual",
)
tracker2.record_execution(
    step=map_op, step_index=1,
    datasets_before={"transactions": [{"product_name": "  widget  ", "qty": 3}]},
    output_data=[{"product_name": "widget", "qty": 3}],
)
records = tracker2.store.get_all()
test("Map produces 1 lineage record", len(records) == 1, f"got {len(records)}")
test("Record operator_type is Map", records[0].operator_type == "Map")
test("Record output_field is product_name", records[0].output_field == "product_name")
test("Record input_fields is [product_name]", records[0].input_fields == ["product_name"])

# ---------------------------------------------------------------------------
# Test 3: Join records the join rule (FR-PROV-02)
# ---------------------------------------------------------------------------
print("\nTest 3: Join records join rule")
tracker3 = LineageTracker()
join_op = JoinOperator(
    op="Join", left="transactions", right="products",
    on=[JoinKeyPair(left_key="product_id", right_key="product_id")],
    type="left", output="joined", source="manual",
)
tracker3.record_execution(
    step=join_op, step_index=2,
    datasets_before={
        "transactions": [{"product_id": 1, "qty": 3}],
        "products": [{"product_id": 1, "name": "widget"}],
    },
    output_data=[{"record": {"product_id": 1, "qty": 3}, "matched": {"product_id": 1, "name": "widget"}}],
)
records = tracker3.store.get_all()
test("Join produces lineage records", len(records) > 0, f"got {len(records)}")
join_recs = [r for r in records if r.operator_type == "Join"]
test("All records are Join type", all(r.operator_type == "Join" for r in join_recs))
test("Join rule is recorded", join_recs[0].join_rule is not None)
test("Join rule contains both table names", "transactions" in join_recs[0].join_rule and "products" in join_recs[0].join_rule)
test("Join rule contains the key pair", "product_id" in join_recs[0].join_rule)
test("Join type is left", join_recs[0].join_type == "left")
test("Source origin is manual", join_recs[0].source_origin == "manual")

# ---------------------------------------------------------------------------
# Test 4: Join from agent Proposal records confidence + review status (FR-PROV-02)
# ---------------------------------------------------------------------------
print("\nTest 4: Agent-inferred Join records confidence and review status")
tracker4 = LineageTracker()
# Simulate a join that came from an agent Proposal with confidence 0.85
agent_join_op = JoinOperator(
    op="Join", left="transactions", right="products",
    on=[JoinKeyPair(left_key="product_id", right_key="product_variation_id")],
    type="left", output="joined_clv",
    source="agent_inferred", confidence=0.85, review_status="pending",
)
tracker4.record_execution(
    step=agent_join_op, step_index=3,
    datasets_before={
        "transactions": [{"product_id": 1, "revenue": 100}],
        "products": [{"product_variation_id": 1, "name": "widget"}],
    },
    output_data=[{"record": {"product_id": 1, "revenue": 100}, "matched": {"product_variation_id": 1, "name": "widget"}}],
)
records = tracker4.store.get_all()
join_recs = [r for r in records if r.operator_type == "Join"]
test("Agent join has source_origin=agent_inferred", join_recs[0].source_origin == "agent_inferred")
test("Agent join has confidence=0.85", join_recs[0].confidence == 0.85, f"got {join_recs[0].confidence}")
test("Agent join has review_status=pending", join_recs[0].review_status == "pending")
test("Agent join rule mentions product_variation_id", "product_variation_id" in join_recs[0].join_rule)

# ---------------------------------------------------------------------------
# Test 5: Query interface traces output field back to source (FR-PROV-03)
# ---------------------------------------------------------------------------
print("\nTest 5: Query traces field back to source without re-execution")
tracker5 = LineageTracker()
# Simulate a 2-step pipeline: Map then Join
map_op5 = MapOperator(
    op="Map", input="raw_products", output="products",
    transform="normalize", source="manual",
)
tracker5.record_execution(
    step=map_op5, step_index=0,
    datasets_before={"raw_products": [{"name": "widget", "price": 10}]},
    output_data=[{"name": "widget", "price": 10, "product_id": 1}],
)
join_op5 = JoinOperator(
    op="Join", left="transactions", right="products",
    on=[JoinKeyPair(left_key="pid", right_key="product_id")],
    type="left", output="joined", source="agent_inferred", confidence=0.92, review_status="approved",
)
tracker5.record_execution(
    step=join_op5, step_index=1,
    datasets_before={
        "transactions": [{"pid": 1, "qty": 3}],
        "products": [{"product_id": 1, "name": "widget"}],
    },
    output_data=[{"record": {"pid": 1, "qty": 3}, "matched": {"product_id": 1, "name": "widget"}}],
)
query5 = LineageQuery(tracker5.store)
chain = query5.trace_field("joined", "record")
test("Trace returns lineage records", len(chain) > 0, f"got {len(chain)}")
test("Trace does not re-run pipeline (returns stored records)", all(isinstance(r, LineageRecord) for r in chain))

# ---------------------------------------------------------------------------
# Test 6: Query provides human-readable explanation (FR-PROV-03)
# ---------------------------------------------------------------------------
print("\nTest 6: Query provides human-readable explanation")
query6 = LineageQuery(tracker5.store)
explanation = query6.explain_field("joined", "record")
test("Explanation is a string", isinstance(explanation, str))
test("Explanation contains dataset name", "joined" in explanation)
test("Explanation contains field name", "record" in explanation)
test("Explanation contains operator type", "Join" in explanation)
test("Explanation contains join rule", "transactions" in explanation and "products" in explanation)
test("Explanation contains confidence", "0.92" in explanation, f"expected 0.92 in: {explanation}")

# ---------------------------------------------------------------------------
# Test 7: Join provenance distinguishes manual vs agent_inferred
# ---------------------------------------------------------------------------
print("\nTest 7: Join provenance distinguishes manual vs agent")
tracker7 = LineageTracker()
# Manual join
manual_join = JoinOperator(
    op="Join", left="a", right="b",
    on=[JoinKeyPair(left_key="id", right_key="id")],
    type="inner", output="manual_joined", source="manual",
)
tracker7.record_execution(
    step=manual_join, step_index=0,
    datasets_before={"a": [{"id": 1}], "b": [{"id": 1}]},
    output_data=[{"record": {"id": 1}, "matched": {"id": 1}}],
)
# Agent join
agent_join = JoinOperator(
    op="Join", left="c", right="d",
    on=[JoinKeyPair(left_key="cid", right_key="did")],
    type="left", output="agent_joined",
    source="agent_inferred", confidence=0.78, review_status="pending",
)
tracker7.record_execution(
    step=agent_join, step_index=1,
    datasets_before={"c": [{"cid": 1}], "d": [{"did": 1}]},
    output_data=[{"record": {"cid": 1}, "matched": {"did": 1}}],
)
query7 = LineageQuery(tracker7.store)
join_prov = query7.get_join_provenance()
test("Two distinct join provenance entries", len(join_prov) == 2, f"got {len(join_prov)}")
manual_entries = [j for j in join_prov if j["source_origin"] == "manual"]
agent_entries = [j for j in join_prov if j["source_origin"] == "agent_inferred"]
test("One manual join", len(manual_entries) == 1)
test("One agent join", len(agent_entries) == 1)
test("Agent join has confidence", agent_entries[0]["confidence"] == 0.78)
test("Manual join has no confidence", manual_entries[0]["confidence"] is None)

# ---------------------------------------------------------------------------
# Test 8: LineageStore summary reports correct counts
# ---------------------------------------------------------------------------
print("\nTest 8: LineageStore summary")
summary = tracker7.store.summary()
test("Summary has total_records", "total_records" in summary)
test("Summary has by_operator", "by_operator" in summary)
test("Summary has join_records", "join_records" in summary)
test("Summary join_records > 0", summary["join_records"] > 0)
test("Summary by_operator has Join", "Join" in summary["by_operator"])

# ---------------------------------------------------------------------------
# Test 9: Full pipeline with agent Proposal join (the CLV scenario)
# ---------------------------------------------------------------------------
print("\nTest 9: CLV scenario - product_variation_id vs product_id lineage")
# Use JoinInference to create a real agent proposal join
inferrer = JoinInference()
candidates = [
    JoinKeyCandidate(
        left_key="product_id", right_key="product_id",
        left_values=[1, 2, 3], right_values=[1, 1, 2, 2, 3, 3],  # non-unique
        left_type="Integer", right_type="Integer",
    ),
    JoinKeyCandidate(
        left_key="product_id", right_key="product_variation_id",
        left_values=[1, 2, 3], right_values=[1, 2, 3],  # unique
        left_type="Integer", right_type="Integer",
    ),
]
proposals = inferrer.infer_joins(
    left_name="transactions", right_name="products",
    candidates=candidates,
)
test("JoinInference returns 2 proposals", len(proposals) == 2, f"got {len(proposals)}")

# The higher-confidence proposal should be product_variation_id (unique)
best = proposals[0]  # sorted by confidence descending
test("Best proposal is product_variation_id", best.evidence["right_key"] == "product_variation_id",
     f"got {best.evidence['right_key']}")
test("Best proposal has higher confidence", best.confidence > proposals[1].confidence)

# Record lineage for both proposals (simulating both being executed for provenance)
tracker9 = LineageTracker()
for i, proposal in enumerate(proposals):
    if proposal.ir_operator is not None:
        tracker9.record_execution(
            step=proposal.ir_operator, step_index=i,
            datasets_before={
                "transactions": [{"product_id": 1}],
                "products": [{"product_id": 1, "product_variation_id": 1}],
            },
            output_data=[{"record": {"product_id": 1}, "matched": {"product_id": 1}}],
        )

query9 = LineageQuery(tracker9.store)
join_prov = query9.get_join_provenance()
test("CLV scenario has 2 join provenance entries", len(join_prov) == 2, f"got {len(join_prov)}")

# Find the product_variation_id join
var_id_joins = [j for j in join_prov if "product_variation_id" in j["join_rule"]]
test("Found product_variation_id join in provenance", len(var_id_joins) == 1)
test("product_variation_id join is agent_inferred", var_id_joins[0]["source_origin"] == "agent_inferred")
test("product_variation_id join has confidence", var_id_joins[0]["confidence"] is not None)

# Find the product_id join (the rejected one)
pid_joins = [j for j in join_prov if "product_id == product_id" in j["join_rule"]]
test("Found product_id join in provenance", len(pid_joins) == 1)
test("product_id join is agent_inferred", pid_joins[0]["source_origin"] == "agent_inferred")
test("product_id join has lower confidence", pid_joins[0]["confidence"] < var_id_joins[0]["confidence"])

print("\n" + "=" * 60)
print("All Phase 6 smoke tests passed.")
