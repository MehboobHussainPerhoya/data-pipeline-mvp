import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

print("=== Phase 1 Smoke Test ===\n")

# Test 1: Registry loads with built-in types
from schema.type_registry import TypeRegistry
from schema.registry_setup import registry, SCHEMA_REGISTRY, get_schema
from schema.canonical_types import STRING, TIMESTAMP, PHONE_NUMBER

print(f"1. Registry version: {registry.version}")
print(f"2. Built-in types: {registry.type_names}")
assert registry.version == 1, f"Expected version 1, got {registry.version}"
assert "String" in registry.type_names, "String type missing"
assert "Timestamp" in registry.type_names, "Timestamp type missing"
assert "PhoneNumber" in registry.type_names, "PhoneNumber type missing"
print("   PASS\n")

# Test 2: Type-scoped operations (FR-REG-02)
print(f"3. Is 'date_diff' valid for Timestamp? {registry.is_operation_valid('Timestamp', 'date_diff')}")
print(f"   Is 'area_code_extract' valid for String? {registry.is_operation_valid('String', 'area_code_extract')}")
assert registry.is_operation_valid('Timestamp', 'date_diff') == True
assert registry.is_operation_valid('String', 'area_code_extract') == False
print("   PASS\n")

# Test 3: Schema definitions exist and have correct fields
sc = get_schema("SupportCase")
ka = get_schema("KnowledgeArticle")
jo = get_schema("JoinedCaseOutput")

print(f"4. SupportCase fields: {sc.field_names}")
print(f"   Required output fields: {sc.required_output_fields}")
assert "case_id" in sc.field_names
assert "source_system" in sc.field_names
assert "case_id" in sc.required_output_fields
print("   PASS\n")

print(f"5. KnowledgeArticle fields: {ka.field_names}")
assert "question" in ka.field_names
assert "answer" in ka.field_names
print("   PASS\n")

print(f"6. JoinedCaseOutput fields: {jo.field_names}")
assert "matched_article_count" in jo.field_names
print("   PASS\n")

# Test 4: Required column tracking (FR-VAL-01)
record = {"case_id": "test_1", "subject": None, "source_system": "test"}
satisfied, total, missing = sc.required_column_status(record)
print(f"7. Required column status: {satisfied}/{total} satisfied, missing: {missing}")
assert satisfied == 2  # case_id + source_system
assert total == 3  # case_id + subject + source_system
assert "subject" in missing
print("   PASS\n")

# Test 5: Registry versioning (FR-REG-03)
from schema.canonical_types import CanonicalType
custom_type = CanonicalType(
    name="MyCustomType",
    python_type=str,
    description="A custom type for testing",
    valid_operations=["custom_op"],
)
new_version = registry.register_type(custom_type)
print(f"8. After registering custom type, registry version: {registry.version}")
assert new_version == 2
assert "MyCustomType" in registry.type_names
print("   PASS\n")

# Test 6: Historical version retrieval
v1 = registry.get_version(1)
print(f"9. Version 1 has {len(v1.types)} types, version 2 has {len(registry.type_names)} types")
assert "MyCustomType" not in v1.types  # custom type shouldn't be in v1
assert "MyCustomType" in registry.type_names  # but should be in current
print("   PASS\n")

# Test 7: Field validation using registry
from schema.field_definition import FieldDefinition
fd = FieldDefinition("test_field", "String", nullable=False, required_in_output=True)
is_valid, reason = fd.validate("hello", registry)
print(f"10. Field validation (non-null string): valid={is_valid}, reason={reason}")
assert is_valid == True

is_valid, reason = fd.validate(None, registry)
print(f"    Field validation (null on non-nullable): valid={is_valid}, reason={reason}")
assert is_valid == False
print("    PASS\n")

print("=== All Phase 1 smoke tests passed! ===")