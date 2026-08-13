"""
Phase 3 Smoke Test — Type Checker & Contract Validator

Validates the Phase 3 components:
1. TypeChecker — static pre-execution validation of PipelineIR (FR-TC-01/02/03)
2. ContractValidator — post-execution output contract validation (FR-VAL-01/02/03)
3. output_validator.py — validate_pipeline_full() rich API + is_safe_to_deploy() delegation

Key invariant: deploy_gate.py is NOT touched in Phase 3 — it works unchanged
because is_safe_to_deploy() preserves its (bool, list[str]) return shape.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

print("=== Phase 3 Smoke Test ===\n")

# ---------------------------------------------------------------------------
# Test 1: TypeChecker validates the real pipeline IR (FR-TC-01)
# ---------------------------------------------------------------------------
from ir.pipeline_definition import support_case_pipeline_ir
from validation.type_checker import TypeChecker, TypeCheckResult

print("1. Type-checking the support case pipeline IR...")
checker = TypeChecker()
tc_result = checker.check(
    support_case_pipeline_ir,
    registered_mappers={"map_ticket_to_supportcase", "map_kb_to_knowledgearticle", "map_api_to_supportcase"},
)
print(f"   is_valid: {tc_result.is_valid}")
print(f"   errors: {len(tc_result.errors)}")
print(f"   warnings: {len(tc_result.warnings)}")
assert tc_result.is_valid, f"Pipeline IR should be valid, but got errors: {[str(e) for e in tc_result.errors]}"
assert len(tc_result.errors) == 0
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 2: TypeChecker catches invalid IR (bad input reference)
# ---------------------------------------------------------------------------
from ir.operators import CastOperator, MapOperator, JoinOperator, JoinKeyPair
from ir.pipeline_ir import PipelineIR, InputSource

print("2. Type-checking an INVALID IR (bad input reference)...")
bad_ir = PipelineIR(
    name="bad_pipeline",
    inputs=[InputSource(name="tickets", source_type="csv", location="x.csv", schema_name="SupportCase", connector="read_x")],
    steps=[
        CastOperator(op="Cast", input="nonexistent_dataset", field="price", to="Double", output="out"),
    ],
)
bad_result = checker.check(bad_ir)
print(f"   is_valid: {bad_result.is_valid}")
print(f"   errors: {len(bad_result.errors)}")
for e in bad_result.errors:
    print(f"     {e}")
assert not bad_result.is_valid, "Bad IR should fail type checking"
assert len(bad_result.errors) > 0
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 3: TypeChecker catches invalid canonical type in Cast (FR-TC-01)
# ---------------------------------------------------------------------------
print("3. Type-checking Cast to invalid canonical type...")
bad_cast_ir = PipelineIR(
    name="bad_cast",
    inputs=[InputSource(name="tickets", source_type="csv", location="x.csv", schema_name="SupportCase", connector="read_x")],
    steps=[
        CastOperator(op="Cast", input="tickets", field="price", to="NonExistentType", output="out"),
    ],
)
bad_cast_result = checker.check(bad_cast_ir)
print(f"   is_valid: {bad_cast_result.is_valid}")
for e in bad_cast_result.errors:
    print(f"     {e}")
assert not bad_cast_result.is_valid
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 4: TypeChecker catches invalid join type
# ---------------------------------------------------------------------------
print("4. Type-checking invalid join type...")
bad_join_ir = PipelineIR(
    name="bad_join",
    inputs=[
        InputSource(name="left", source_type="csv", location="l.csv", schema_name="SupportCase", connector="read_l"),
        InputSource(name="right", source_type="csv", location="r.csv", schema_name="KnowledgeArticle", connector="read_r"),
    ],
    steps=[
        JoinOperator(
            op="Join", left="left", right="right",
            on=[JoinKeyPair(left_key="ticket_type", right_key="category")],
            type="invalid_join_type", output="out",
        ),
    ],
)
bad_join_result = checker.check(bad_join_ir)
print(f"   is_valid: {bad_join_result.is_valid}")
for e in bad_join_result.errors:
    print(f"     {e}")
assert not bad_join_result.is_valid
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 5: TypeChecker catches invalid aggregation function
# ---------------------------------------------------------------------------
from ir.operators import AggregateOperator, AggregationSpec

print("5. Type-checking invalid aggregation function...")
bad_agg_ir = PipelineIR(
    name="bad_agg",
    inputs=[InputSource(name="data", source_type="csv", location="d.csv", schema_name="SupportCase", connector="read_d")],
    steps=[
        AggregateOperator(
            op="Aggregate", input="data", group_by=["status"],
            agg=[AggregationSpec(field="case_id", fn="invalid_fn", as_="result")],
            output="out",
        ),
    ],
)
bad_agg_result = checker.check(bad_agg_ir)
print(f"   is_valid: {bad_agg_result.is_valid}")
for e in bad_agg_result.errors:
    print(f"     {e}")
assert not bad_agg_result.is_valid
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 6: TypeChecker catches unknown transform in Map operator
# ---------------------------------------------------------------------------
print("6. Type-checking unknown transform in Map operator...")
bad_map_ir = PipelineIR(
    name="bad_map",
    inputs=[InputSource(name="data", source_type="csv", location="d.csv", schema_name="SupportCase", connector="read_d")],
    steps=[
        MapOperator(op="Map", input="data", transform="nonexistent_transform", output="out"),
    ],
)
bad_map_result = checker.check(bad_map_ir)
print(f"   is_valid: {bad_map_result.is_valid}")
for e in bad_map_result.errors:
    print(f"     {e}")
assert not bad_map_result.is_valid
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 7: TypeChecker summary dict
# ---------------------------------------------------------------------------
print("7. TypeChecker summary dict...")
summary = tc_result.summary()
print(f"   summary: {summary}")
assert "is_valid" in summary
assert "error_count" in summary
assert "warning_count" in summary
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 8: ContractValidator on valid output (FR-VAL-01/02)
# ---------------------------------------------------------------------------
from validation.contract_validator import ContractValidator, ContractCheckResult
from schema.output_schema import JoinedCaseOutput

print("8. Contract validation on valid output records...")
valid_records = [
    JoinedCaseOutput(case_id="t_1", subject="Subject 1", ticket_type="Refund", matched_category="REFUND", matched_article_count=3, source_system="tickets"),
    JoinedCaseOutput(case_id="t_2", subject="Subject 2", ticket_type=None, matched_category=None, matched_article_count=0, source_system="api"),
]
validator = ContractValidator()
cv_result = validator.check(valid_records, "JoinedCaseOutput")
print(f"   is_satisfied: {cv_result.is_satisfied}")
print(f"   violations: {len(cv_result.violations)}")
print(f"   required_column_status: {cv_result.required_column_status}")
assert cv_result.is_satisfied
assert len(cv_result.violations) == 0
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 9: ContractValidator catches empty output (FR-VAL-01)
# ---------------------------------------------------------------------------
print("9. Contract validation on empty output...")
empty_result = validator.check([], "JoinedCaseOutput")
print(f"   is_satisfied: {empty_result.is_satisfied}")
for v in empty_result.violations:
    print(f"     {v}")
assert not empty_result.is_satisfied
assert any(v.violation_type == "empty_output" for v in empty_result.violations)
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 10: ContractValidator catches duplicate case_id (FR-VAL-02)
# ---------------------------------------------------------------------------
print("10. Contract validation on duplicate case_id...")
dup_records = [
    JoinedCaseOutput(case_id="t_1", subject="A", ticket_type=None, matched_category=None, matched_article_count=0, source_system="tickets"),
    JoinedCaseOutput(case_id="t_1", subject="B", ticket_type=None, matched_category=None, matched_article_count=0, source_system="tickets"),
]
dup_result = validator.check(dup_records, "JoinedCaseOutput")
print(f"   is_satisfied: {dup_result.is_satisfied}")
for v in dup_result.violations:
    print(f"     {v}")
assert not dup_result.is_satisfied
assert any(v.violation_type == "duplicate" for v in dup_result.violations)
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 11: ContractValidator catches null on non-nullable field (FR-VAL-02)
# ---------------------------------------------------------------------------
print("11. Contract validation on null required field...")
# Build a record with a null subject (which is required in output)
null_records = [
    JoinedCaseOutput(case_id="t_1", subject="", ticket_type=None, matched_category=None, matched_article_count=0, source_system="tickets"),
]
# Manually set subject to None to simulate a null violation
null_records[0] = null_records[0].model_copy(update={"subject": None})
null_result = validator.check(null_records, "JoinedCaseOutput")
print(f"   is_satisfied: {null_result.is_satisfied}")
for v in null_result.violations:
    print(f"     {v}")
assert not null_result.is_satisfied
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 12: ContractValidator breaking-change detection (FR-VAL-03)
# ---------------------------------------------------------------------------
print("12. Contract breaking-change detection...")
# Check that comparing against a previous contract that had more required fields
# produces a warning (not an error)
from schema.schema_definition import SchemaDefinition
from schema.field_definition import FieldDefinition
from schema.registry_setup import SCHEMA_REGISTRY

# Register a "previous" contract with an extra required field
prev_contract = SchemaDefinition(
    name="JoinedCaseOutput_v1",
    fields=[
        FieldDefinition("case_id", "String", nullable=False, required_in_output=True),
        FieldDefinition("subject", "String", nullable=False, required_in_output=True),
        FieldDefinition("deprecated_field", "String", nullable=False, required_in_output=True),  # dropped in current
    ],
)
SCHEMA_REGISTRY["JoinedCaseOutput_v1"] = prev_contract

bc_result = validator.check(valid_records, "JoinedCaseOutput", previous_contract_name="JoinedCaseOutput_v1")
print(f"   is_satisfied: {bc_result.is_satisfied}")
print(f"   warnings: {bc_result.warnings}")
assert len(bc_result.warnings) > 0, "Should have a breaking-change warning about dropped required column"
assert "deprecated_field" in bc_result.warnings[0]
print("   PASS\n")

# Clean up the temporary schema
del SCHEMA_REGISTRY["JoinedCaseOutput_v1"]

# ---------------------------------------------------------------------------
# Test 13: ContractCheckResult summary
# ---------------------------------------------------------------------------
print("13. ContractCheckResult summary...")
cv_summary = cv_result.summary()
print(f"   summary: {cv_summary}")
assert "is_satisfied" in cv_summary
assert "violation_count" in cv_summary
assert "required_column_status" in cv_summary
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 14: validate_pipeline_full() rich API
# ---------------------------------------------------------------------------
from validation.output_validator import validate_pipeline_full, is_safe_to_deploy

print("14. validate_pipeline_full() rich API...")
full_result = validate_pipeline_full(valid_records, [])
print(f"   {full_result}")
assert full_result["is_safe"] == True
assert full_result["violation_count"] == 0
assert "contract_status" in full_result
assert "warnings" in full_result
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 15: is_safe_to_deploy() delegates to validate_pipeline_full (backward compat)
# ---------------------------------------------------------------------------
print("15. is_safe_to_deploy() backward compatibility...")
is_safe, reasons = is_safe_to_deploy(valid_records, [])
print(f"   is_safe: {is_safe}, reasons: {reasons}")
assert is_safe == True
assert reasons == []
# Return shape must be (bool, list[str]) — what deploy_gate.py expects
assert isinstance(is_safe, bool)
assert isinstance(reasons, list)
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 16: is_safe_to_deploy() with build errors
# ---------------------------------------------------------------------------
print("16. is_safe_to_deploy() with build errors...")
is_safe_err, reasons_err = is_safe_to_deploy(valid_records, [("rec_1", "some error")])
print(f"   is_safe: {is_safe_err}, reasons: {reasons_err}")
assert is_safe_err == False
assert len(reasons_err) > 0
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 17: Full pipeline — TypeChecker → Executor → ContractValidator
# This is the end-to-end Phase 3 integration test
# ---------------------------------------------------------------------------
print("17. End-to-end: TypeChecker -> Executor -> ContractValidator...")

from ir.executor import IRExecutor
from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api
from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase
from validation.output_validator import build_output_records
import copy

PROJECT_ROOT = Path(__file__).resolve().parent

# Step 1: Type-check the IR before execution
checker = TypeChecker()
tc_result = checker.check(
    support_case_pipeline_ir,
    registered_mappers={"map_ticket_to_supportcase", "map_kb_to_knowledgearticle", "map_api_to_supportcase"},
)
assert tc_result.is_valid, f"IR type check failed: {[str(e) for e in tc_result.errors]}"
print(f"   Type check: PASS ({len(tc_result.errors)} errors)")

# Step 2: Execute the IR
executor = IRExecutor()
executor.register_connector("read_support_tickets", read_support_tickets)
executor.register_connector("read_kb_articles", read_kb_articles)
executor.register_connector("read_support_activity_api", read_support_activity_api)
executor.register_mapper("map_ticket_to_supportcase", map_ticket_to_supportcase)
executor.register_mapper("map_kb_to_knowledgearticle", map_kb_to_knowledgearticle)
executor.register_mapper("map_api_to_supportcase", map_api_to_supportcase)

ir = copy.deepcopy(support_case_pipeline_ir)
for source in ir.inputs:
    if source.source_type == "csv":
        source.location = str(PROJECT_ROOT / source.location)

datasets = executor.execute(ir, connector_params={
    "read_support_activity_api": {
        "fallback_path": str(PROJECT_ROOT / "data/sample/support_activity_api/sample.json"),
    }
})
joined = datasets.get("joined", [])
output_records, errors = build_output_records(joined)
print(f"   Execution: {len(output_records)} records, {len(errors)} build errors")

# Step 3: Contract validation on the output
validator = ContractValidator()
cv_result = validator.check(output_records, "JoinedCaseOutput")
print(f"   Contract: satisfied={cv_result.is_satisfied}, violations={len(cv_result.violations)}")
assert cv_result.is_satisfied, f"Contract violations: {[str(v) for v in cv_result.violations]}"
print("   PASS\n")

# ---------------------------------------------------------------------------
# Test 18: deploy_gate.py still works unchanged (Phase 3 invariant)
# ---------------------------------------------------------------------------
print("18. deploy_gate.py unchanged invariant check...")
from validation.deploy_gate import deploy_pipeline
import tempfile

# Safe to deploy + approved → should succeed
with tempfile.TemporaryDirectory() as tmpdir:
    output_path = str(Path(tmpdir) / "output.json")
    deploy_pipeline(output_records[:5], True, [], approved=True, output_path=output_path)
    assert Path(output_path).exists()
    print(f"   Deployed 5 records to {output_path}")

# Not safe → should block
with tempfile.TemporaryDirectory() as tmpdir:
    output_path = str(Path(tmpdir) / "output.json")
    try:
        deploy_pipeline(output_records[:5], False, ["unsafe"], approved=True, output_path=output_path)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        print(f"   Correctly blocked unsafe deploy: {e}")

# Not approved → should block
with tempfile.TemporaryDirectory() as tmpdir:
    output_path = str(Path(tmpdir) / "output.json")
    try:
        deploy_pipeline(output_records[:5], True, [], approved=False, output_path=output_path)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        print(f"   Correctly blocked unapproved deploy: {e}")

print("   PASS\n")

print("=== All Phase 3 smoke tests passed! ===")