"""
Contract Validator — checks pipeline output against its declared output contract.

The Contract Validator runs *after* execution (on actual output records) and checks:
- Required columns are present and non-null (FR-VAL-01)
- Nullability constraints are satisfied (FR-VAL-02)
- Uniqueness constraints on declared fields (FR-VAL-02)
- Breaking-change detection: warns when a change drops a required column (FR-VAL-03)

This is what is_safe_to_deploy() delegates to for contract-level checks.

FSD requirements:
- FR-VAL-01: Required-column tracking
- FR-VAL-02: Nullability & constraint checks
- FR-VAL-03: Breaking-change warning
"""

from dataclasses import dataclass
from typing import Optional, Any
from schema.registry_setup import SCHEMA_REGISTRY, get_schema
from schema.schema_definition import SchemaDefinition


@dataclass
class ContractViolation:
    """One violation of the output contract."""
    record_index: Optional[int]   # None for pipeline-level violations
    field: str
    violation_type: str           # "missing_required", "null_violation", "duplicate", "empty_output"
    message: str

    def __str__(self):
        loc = f"record {self.record_index}" if self.record_index is not None else "pipeline"
        return f"  [{loc}] {self.field}: {self.violation_type} — {self.message}"


@dataclass
class ContractCheckResult:
    """Result of contract validation."""
    is_satisfied: bool
    violations: list[ContractViolation]
    required_column_status: dict   # {"satisfied": int, "total": int, "missing": [str]}
    warnings: list[str]

    def summary(self) -> dict:
        return {
            "is_satisfied": self.is_satisfied,
            "violation_count": len(self.violations),
            "violations": [str(v) for v in self.violations],
            "required_column_status": self.required_column_status,
            "warnings": self.warnings,
        }


class ContractValidator:
    """
    Validates output records against a declared output contract (SchemaDefinition).

    Usage:
        validator = ContractValidator()
        result = validator.check(output_records, contract_name="JoinedCaseOutput")
        if not result.is_satisfied:
            for v in result.violations:
                print(v)
    """

    def check(
        self,
        output_records: list[Any],
        contract_name: str,
        previous_contract_name: str = None,
    ) -> ContractCheckResult:
        """
        Validate output records against the contract.

        output_records: list of Pydantic model instances or dicts
        contract_name: name of the SchemaDefinition to validate against
        previous_contract_name: optional — the previous contract, for breaking-change detection
        """
        contract = get_schema(contract_name)
        violations: list[ContractViolation] = []
        warnings: list[str] = []

        # --- Check 1: Empty output ---
        if len(output_records) == 0:
            violations.append(ContractViolation(
                record_index=None, field="_all", violation_type="empty_output",
                message="Output is empty — nothing to deploy.",
            ))
            return ContractCheckResult(
                is_satisfied=False,
                violations=violations,
                required_column_status={"satisfied": 0, "total": len(contract.required_output_fields), "missing": contract.required_output_fields},
                warnings=warnings,
            )

        # --- Check 2: Required-column tracking (FR-VAL-01) ---
        # Check the first record for required column presence
        first_record = self._to_dict(output_records[0])
        satisfied, total, missing = contract.required_column_status(first_record)

        # --- Check 3: Per-record nullability & required field checks (FR-VAL-02) ---
        for i, record in enumerate(output_records):
            rec_dict = self._to_dict(record)

            for field_def in contract.fields:
                value = rec_dict.get(field_def.name)

                # Required-in-output fields must be non-null
                if field_def.required_in_output and value is None:
                    violations.append(ContractViolation(
                        record_index=i, field=field_def.name,
                        violation_type="missing_required",
                        message=f"Required output field '{field_def.name}' is null.",
                    ))

                # Non-nullable fields must not be null
                if not field_def.nullable and value is None:
                    violations.append(ContractViolation(
                        record_index=i, field=field_def.name,
                        violation_type="null_violation",
                        message=f"Field '{field_def.name}' is not nullable but got null.",
                    ))

        # --- Check 4: Uniqueness check on case_id (FR-VAL-02) ---
        # case_id is the primary key — check for duplicates
        case_ids = [self._to_dict(r).get("case_id") for r in output_records]
        seen_ids = set()
        duplicate_ids = set()
        for cid in case_ids:
            if cid in seen_ids:
                duplicate_ids.add(cid)
            seen_ids.add(cid)

        if duplicate_ids:
            violations.append(ContractViolation(
                record_index=None, field="case_id",
                violation_type="duplicate",
                message=f"{len(duplicate_ids)} duplicate case_id(s) found: {sorted(duplicate_ids)[:5]}{'...' if len(duplicate_ids) > 5 else ''}",
            ))

        # --- Check 5: Breaking-change detection (FR-VAL-03) ---
        if previous_contract_name:
            try:
                prev_contract = get_schema(previous_contract_name)
                prev_required = set(prev_contract.required_output_fields)
                curr_required = set(contract.required_output_fields)
                dropped = prev_required - curr_required
                if dropped:
                    warnings.append(
                        f"Breaking change: {len(dropped)} required column(s) dropped from contract: {dropped}. "
                        f"These columns were required in '{previous_contract_name}' but are not in '{contract_name}'."
                    )
            except ValueError:
                pass  # previous contract doesn't exist — skip

        is_satisfied = len(violations) == 0
        return ContractCheckResult(
            is_satisfied=is_satisfied,
            violations=violations,
            required_column_status={"satisfied": satisfied, "total": total, "missing": missing},
            warnings=warnings,
        )

    def _to_dict(self, record: Any) -> dict:
        """Convert a record to dict if it's a Pydantic model."""
        if isinstance(record, dict):
            return record
        if hasattr(record, "model_dump"):
            return record.model_dump()
        return dict(record)