import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

print("=== Phase 2 Smoke Test ===\n")

# ---------------------------------------------------------------------------
# Test 1: IR operators can be created and serialized
# ---------------------------------------------------------------------------
from ir.operators import CastOperator, MapOperator, JoinOperator, JoinKeyPair, UnionOperator

print("1. Creating IR operators...")
cast = CastOperator(op="Cast", input="tickets", field="price", to="Double", output="tickets_cast")
assert cast.op == "Cast"
assert cast.input == "tickets"
assert cast.to == "Double"
print(f"   CastOperator: {cast.model_dump()}")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 2: Pipeline IR is correctly defined
# ---------------------------------------------------------------------------
from ir.pipeline_definition import support_case_pipeline_ir

print(f"2. Pipeline IR: '{support_case_pipeline_ir.name}'")
print(f"   Inputs: {len(support_case_pipeline_ir.inputs)} sources")
print(f"   Steps: {len(support_case_pipeline_ir.steps)} operators")
print(f"   Output contract: {support_case_pipeline_ir.output_contract}")
assert support_case_pipeline_ir.name == "support_case_pipeline"
assert len(support_case_pipeline_ir.inputs) == 3
assert len(support_case_pipeline_ir.steps) == 5  # 3 maps + 1 union + 1 join
assert support_case_pipeline_ir.output_contract == "JoinedCaseOutput"
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 3: IR summary and JSON export
# ---------------------------------------------------------------------------
summary = support_case_pipeline_ir.to_summary()
print(f"3. IR summary: {summary}")
assert summary["step_count"] == 5
assert summary["input_count"] == 3
print("   PASS\n")

from ir.exporter import export_ir_json, export_ir_pseudocode
ir_json = export_ir_json(support_case_pipeline_ir)
assert "support_case_pipeline" in ir_json
print(f"4. IR JSON export: {len(ir_json)} chars")
print("   PASS\n")

ir_pseudo = export_ir_pseudocode(support_case_pipeline_ir)
assert "Pipeline: support_case_pipeline" in ir_pseudo
print(f"5. IR pseudocode export:")
print(ir_pseudo)
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 4: Executor produces same output as old imperative code
# This is the critical test — the IR execution must match the old run_pipeline()
# ---------------------------------------------------------------------------
print("6. Running pipeline via IR executor...")

from ir.executor import IRExecutor
from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api
from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase
from validation.output_validator import build_output_records, is_safe_to_deploy

PROJECT_ROOT = Path(__file__).resolve().parent

# Build executor
executor = IRExecutor()
executor.register_connector("read_support_tickets", read_support_tickets)
executor.register_connector("read_kb_articles", read_kb_articles)
executor.register_connector("read_support_activity_api", read_support_activity_api)
executor.register_mapper("map_ticket_to_supportcase", map_ticket_to_supportcase)
executor.register_mapper("map_kb_to_knowledgearticle", map_kb_to_knowledgearticle)
executor.register_mapper("map_api_to_supportcase", map_api_to_supportcase)

# Resolve paths
import copy
ir = copy.deepcopy(support_case_pipeline_ir)
for source in ir.inputs:
    if source.source_type == "csv":
        source.location = str(PROJECT_ROOT / source.location)

# Execute via IR
datasets = executor.execute(ir, connector_params={
    "read_support_activity_api": {
        "fallback_path": str(PROJECT_ROOT / "data/sample/support_activity_api/sample.json"),
    }
})
joined_ir = datasets.get("joined", [])
output_ir, errors_ir = build_output_records(joined_ir)

print(f"   IR execution: {len(joined_ir)} joined rows, {len(output_ir)} output records, {len(errors_ir)} errors")

# Now run the old imperative way for comparison
print("   Running old imperative pipeline for comparison...")
tickets_raw = read_support_tickets(str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv"))
kb_raw = read_kb_articles(str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv"))
api_raw = read_support_activity_api(fallback_path=str(PROJECT_ROOT / "data/sample/support_activity_api/sample.json"))

cases_old = [map_ticket_to_supportcase(r) for r in tickets_raw] + \
            [map_api_to_supportcase(r) for r in api_raw]
articles_old = [map_kb_to_knowledgearticle(r, i) for i, r in enumerate(kb_raw)]

from transform.join_engine import join_cases_to_articles
from transform.join_config import TICKET_TYPE_TO_KB_CATEGORY
joined_old = join_cases_to_articles(cases_old, articles_old, TICKET_TYPE_TO_KB_CATEGORY)
output_old, errors_old = build_output_records(joined_old)

print(f"   Old execution: {len(joined_old)} joined rows, {len(output_old)} output records, {len(errors_old)} errors")

# Compare
assert len(joined_ir) == len(joined_old), f"Joined row count mismatch: IR={len(joined_ir)}, old={len(joined_old)}"
assert len(output_ir) == len(output_old), f"Output record count mismatch: IR={len(output_ir)}, old={len(output_old)}"
assert len(errors_ir) == len(errors_old), f"Error count mismatch: IR={len(errors_ir)}, old={len(errors_old)}"

# Compare actual output content
for i, (ir_rec, old_rec) in enumerate(zip(output_ir, output_old)):
    assert ir_rec.model_dump() == old_rec.model_dump(), f"Record {i} mismatch:\n  IR:  {ir_rec.model_dump()}\n  Old: {old_rec.model_dump()}"

print("   OUTPUT IS IDENTICAL between IR executor and old imperative code.")
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 5: Operator types used
# ---------------------------------------------------------------------------
ops_used = support_case_pipeline_ir.operator_types_used()
print(f"7. Operator types used: {ops_used}")
assert "Map" in ops_used
assert "Union" in ops_used
assert "Join" in ops_used
print("   PASS\n")

print("=== All Phase 2 smoke tests passed! ===")