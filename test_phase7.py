"""
Phase 7 smoke tests — Common Model / Ontology Layer (FSD 4.12).

Tests:
1. Object type correctly maps rows from a real support-case dataset (FR-ONT-01)
2. Relationship correctly derived from a real join key — proves product_variation_id,
   not product_id, for the CLV case (FR-ONT-02)
3. FR-ONT-03 — calling the ontology mapper does not change the underlying output
   dataset or trigger re-execution (hash/compare before and after)
4. Object types reference existing SchemaDefinitions — no field redefinition
5. Relationships carry provenance from the IR operator (source, confidence, review_status)
6. The support-case pipeline's real relationship (ticket_type -> category) is derived correctly
"""

import sys
import json
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ontology.object_types import (
    BusinessObjectType, OBJECT_TYPE_REGISTRY, get_object_type,
    support_case_type, knowledge_article_type,
    customer_type, product_type, transaction_type,
)
from ontology.relationships import (
    Relationship, RelationshipKey, derive_relationships_from_ir,
)
from ontology.mapper import OntologyMapper, OntologyMappingResult, ObjectInstance
from ir.pipeline_ir import PipelineIR, InputSource
from ir.operators import JoinOperator, JoinKeyPair, MapOperator, UnionOperator
from ir.pipeline_definition import support_case_pipeline_ir
from transform.join_config import TICKET_TYPE_TO_KB_CATEGORY


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        raise AssertionError(f"{name} failed: {detail}")


def hash_records(records) -> str:
    """Stable hash of a list of records for before/after comparison."""
    serialized = json.dumps(
        [r.model_dump() if hasattr(r, "model_dump") else r for r in records],
        sort_keys=True, default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


print("Phase 7 smoke tests — Common Model / Ontology Layer")
print("=" * 60)

# ---------------------------------------------------------------------------
# Test 1: Object type correctly maps rows from a real dataset (FR-ONT-01)
# Uses the real support-case pipeline output, not synthetic placeholders.
# ---------------------------------------------------------------------------
print("\nTest 1: Object type maps real support-case rows (FR-ONT-01)")

# Run the real pipeline to get real output records
from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api
from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase
from transform.join_engine import join_cases_to_articles
from validation.output_validator import build_output_records

PROJECT_ROOT = Path(__file__).resolve().parent

tickets_raw = read_support_tickets(str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv"))
kb_raw = read_kb_articles(str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv"))
api_raw = read_support_activity_api(
    fallback_path=str(PROJECT_ROOT / "data/sample/support_activity_api/sample.json")
)
cases = [map_ticket_to_supportcase(r) for r in tickets_raw] + \
        [map_api_to_supportcase(r) for r in api_raw]
articles = [map_kb_to_knowledgearticle(r, i) for i, r in enumerate(kb_raw)]
joined = join_cases_to_articles(cases, articles, TICKET_TYPE_TO_KB_CATEGORY)
output_records, errors = build_output_records(joined)

test("Real pipeline produced output records", len(output_records) > 0, f"got {len(output_records)}")
print(f"   Real output: {len(output_records)} records, {len(errors)} validation errors")

# Map the real output records to the SupportCase object type
mapper = OntologyMapper()
result1 = mapper.map_records(
    records=output_records,
    object_type_name="SupportCase",
    ir=support_case_pipeline_ir,
)

test("Mapping returned an OntologyMappingResult", isinstance(result1, OntologyMappingResult))
test("All records mapped to instances", len(result1.instances) == len(output_records),
     f"got {len(result1.instances)} instances for {len(output_records)} records")
test("No unmapped records", result1.unmapped_count == 0, f"got {result1.unmapped_count} unmapped")
test("Total records matches", result1.total_records == len(output_records))
test("Object type name is SupportCase", result1.object_type_name == "SupportCase")

# Verify the instances have real identity values from the real data
first_instance = result1.instances[0]
test("Instance has case_id identity", first_instance.identity is not None)
test("Instance record is a dict snapshot", isinstance(first_instance.record, dict))
test("Instance record has case_id", "case_id" in first_instance.record)
print(f"   First instance identity: {first_instance.identity}")

# ---------------------------------------------------------------------------
# Test 2: Relationship derived from real join key — product_variation_id (FR-ONT-02)
# This is THE critical test: proves the relationship uses product_variation_id,
# not product_id, for the CLV case.
# ---------------------------------------------------------------------------
print("\nTest 2: Relationship uses product_variation_id, not product_id (FR-ONT-02)")

# Build the CLV pipeline IR with the CORRECTED join (from FSD Section 7, step 8)
# This is the same join structure used in test_phase6.py and FSD Appendix A
clv_ir = PipelineIR(
    name="customer_lifetime_value",
    inputs=[
        InputSource(name="transactions", source_type="csv", location="", schema_name="Transaction", connector=""),
        InputSource(name="products", source_type="csv", location="", schema_name="Product", connector=""),
        InputSource(name="customers", source_type="csv", location="", schema_name="Customer", connector=""),
    ],
    steps=[
        # The corrected join: product_id -> product_variation_id (NOT product_id -> product_id)
        JoinOperator(
            op="Join", left="transactions", right="products",
            on=[JoinKeyPair(left_key="product_id", right_key="product_variation_id")],
            type="left", output="joined_clv",
            source="human_override", confidence=None, review_status="approved",
        ),
        # Second join: customer_id -> customer_id
        JoinOperator(
            op="Join", left="joined_clv", right="customers",
            on=[JoinKeyPair(left_key="customer_id", right_key="customer_id")],
            type="left", output="clv_final",
            source="agent_inferred", confidence=0.98, review_status="approved",
        ),
    ],
    output_contract="customer_lifetime_value",
)

relationships2 = derive_relationships_from_ir(clv_ir)
test("CLV IR produces 2 relationships", len(relationships2) == 2, f"got {len(relationships2)}")

# Find the Transaction -> Product relationship
txn_product_rels = [r for r in relationships2 if r.left_object_type.name == "Transaction" and r.right_object_type.name == "Product"]
test("Found Transaction -> Product relationship", len(txn_product_rels) == 1,
     f"got {len(txn_product_rels)}")
txn_product_rel = txn_product_rels[0]

# THE critical assertion: the right key is product_variation_id, NOT product_id
test("Relationship right key is product_variation_id",
     txn_product_rel.keys[0].right_key == "product_variation_id",
     f"got '{txn_product_rel.keys[0].right_key}'")
test("Relationship left key is product_id",
     txn_product_rel.keys[0].left_key == "product_id",
     f"got '{txn_product_rel.keys[0].left_key}'")
test("Relationship is NOT product_id -> product_id",
     not (txn_product_rel.keys[0].left_key == "product_id" and txn_product_rel.keys[0].right_key == "product_id"),
     "FAIL: relationship incorrectly uses product_id -> product_id")

# Verify the describe() method explicitly names the correct key
desc = txn_product_rel.describe()
test("describe() mentions product_variation_id", "product_variation_id" in desc)
test("describe() mentions Transaction and Product", "Transaction" in desc and "Product" in desc)
print(f"   Relationship: {desc}")

# Verify provenance is carried from the IR operator
test("Relationship source_origin is human_override", txn_product_rel.source_origin == "human_override")
test("Relationship review_status is approved", txn_product_rel.review_status == "approved")
test("Relationship source_ir_step_index is 0", txn_product_rel.source_ir_step_index == 0)

# Also check the Customer relationship
cust_rels = [r for r in relationships2 if r.right_object_type.name == "Customer"]
test("Found -> Customer relationship", len(cust_rels) == 1)
test("Customer relationship uses customer_id key", cust_rels[0].keys[0].right_key == "customer_id")
test("Customer relationship is agent_inferred", cust_rels[0].source_origin == "agent_inferred")
test("Customer relationship has confidence 0.98", cust_rels[0].confidence == 0.98)

# ---------------------------------------------------------------------------
# Test 3: FR-ONT-03 — mapper does not change output or trigger re-execution
# This is the highest-priority requirement. We hash the output before and
# after calling the mapper and prove they are identical.
# ---------------------------------------------------------------------------
print("\nTest 3: FR-ONT-03 — backward propagation protection")

# Take a hash of the output records BEFORE calling the mapper
hash_before = hash_records(output_records)

# Also snapshot the IR's JSON representation before
ir_json_before = support_case_pipeline_ir.to_json()

# Call the mapper multiple times
result3a = mapper.map_records(
    records=output_records,
    object_type_name="SupportCase",
    ir=support_case_pipeline_ir,
)
result3b = mapper.map_records(
    records=output_records,
    object_type_name="SupportCase",
    ir=support_case_pipeline_ir,
)

# Hash the output records AFTER calling the mapper
hash_after = hash_records(output_records)
ir_json_after = support_case_pipeline_ir.to_json()

# THE critical assertions: nothing changed
test("Output hash unchanged after mapping", hash_before == hash_after,
     f"hash changed: {hash_before[:16]}... -> {hash_after[:16]}...")
test("IR JSON unchanged after mapping", ir_json_before == ir_json_after,
     "IR was mutated by the mapper")
test("Result flag ir_unchanged is True", result3a.ir_unchanged is True)
test("Result flag output_unchanged is True", result3a.output_unchanged is True)

# Verify the mapper returned a NEW object, not a reference to the input
test("Result is a separate object", result3a is not output_records)
test("Result instances are separate from input records", result3a.instances[0].record is not output_records[0])

# Verify the record snapshots are copies, not references to the originals
original_first_dict = output_records[0].model_dump()
test("Snapshot is a copy, not a reference", result3a.instances[0].record is not original_first_dict)
test("Snapshot has same values as original", result3a.instances[0].record == original_first_dict)

# Prove the mapper has no write/mutate/re-execute methods
mapper_methods = [m for m in dir(mapper) if not m.startswith("_")]
write_methods = [m for m in mapper_methods if any(w in m.lower() for w in ["write", "mutate", "deploy", "execute", "run", "save", "commit"])]
test("Mapper has no write/mutate/deploy/execute methods", len(write_methods) == 0,
     f"found: {write_methods}")
print(f"   Mapper methods: {mapper_methods}")

# ---------------------------------------------------------------------------
# Test 4: Object types reference existing SchemaDefinitions — no redefinition
# ---------------------------------------------------------------------------
print("\nTest 4: Object types reference existing SchemaDefinitions")

# SupportCase and KnowledgeArticle use schemas from SCHEMA_REGISTRY
from schema.registry_setup import SCHEMA_REGISTRY
test("SupportCase type uses registry schema", support_case_type.schema is SCHEMA_REGISTRY["SupportCase"])
test("KnowledgeArticle type uses registry schema", knowledge_article_type.schema is SCHEMA_REGISTRY["KnowledgeArticle"])

# Object types delegate field_names to their schema
test("SupportCase field_names match schema", support_case_type.field_names == SCHEMA_REGISTRY["SupportCase"].field_names)
test("SupportCase required_output_fields match schema",
     support_case_type.required_output_fields == SCHEMA_REGISTRY["SupportCase"].required_output_fields)

# CLV types have real fields with real types
test("Product type identity is product_variation_id", product_type.identity_field == "product_variation_id")
test("Product type has product_variation_id field", "product_variation_id" in product_type.field_names)
test("Product type has product_id field", "product_id" in product_type.field_names)
test("Transaction type has product_id field", "product_id" in transaction_type.field_names)
test("Customer type has customer_id field", "customer_id" in customer_type.field_names)

# ---------------------------------------------------------------------------
# Test 5: Relationships carry provenance from the IR operator
# ---------------------------------------------------------------------------
print("\nTest 5: Relationships carry provenance from IR operators")

# Use the support-case pipeline's real IR
rels5 = derive_relationships_from_ir(support_case_pipeline_ir)
test("Support-case IR produces relationships", len(rels5) >= 1, f"got {len(rels5)}")

# The support-case pipeline has a mapping-based join: ticket_type -> category
sc_rel = rels5[0]
test("Support-case relationship has mapping key", sc_rel.keys[0].has_mapping is True)
test("Support-case relationship left key is ticket_type", sc_rel.keys[0].left_key == "ticket_type")
test("Support-case relationship right key is category", sc_rel.keys[0].right_key == "category")
test("Support-case relationship source is manual", sc_rel.source_origin == "manual")
test("Support-case relationship has mapping dict", sc_rel.keys[0].mapping is not None)
test("Mapping dict is the TICKET_TYPE_TO_KB_CATEGORY", sc_rel.keys[0].mapping == TICKET_TYPE_TO_KB_CATEGORY)
print(f"   Support-case relationship: {sc_rel.describe()}")

# ---------------------------------------------------------------------------
# Test 6: Full end-to-end — map records AND get relationships in one call
# ---------------------------------------------------------------------------
print("\nTest 6: Full ontology mapping with relationships")

result6 = mapper.map_records(
    records=output_records,
    object_type_name="SupportCase",
    ir=support_case_pipeline_ir,
)
test("Result has instances", len(result6.instances) > 0)
test("Result has relationships", len(result6.relationships) > 0)
test("Result ir_unchanged is True", result6.ir_unchanged)
test("Result output_unchanged is True", result6.output_unchanged)

# Serialize the full result
result_dict = result6.to_dict()
test("Result dict has object_type_name", "object_type_name" in result_dict)
test("Result dict has instances", "instances" in result_dict)
test("Result dict has relationships", "relationships" in result_dict)
test("Result dict has ir_unchanged flag", "ir_unchanged" in result_dict)
test("Result dict has output_unchanged flag", "output_unchanged" in result_dict)

print("\n" + "=" * 60)
print("All Phase 7 smoke tests passed.")