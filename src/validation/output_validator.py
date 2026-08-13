from schema.output_schema import JoinedCaseOutput
from .contract_validator import ContractValidator, ContractCheckResult


def build_output_records(joined: list[dict]) -> tuple[list[JoinedCaseOutput], list[tuple]]:
    """
    Converts join_engine output into final validated output records.
    Any record that fails validation is captured as an error, not silently dropped.
    """
    output_records = []
    errors = []

    for item in joined:
        case = item["case"]
        try:
            record = JoinedCaseOutput(
                case_id=case.case_id,
                subject=case.subject,
                ticket_type=case.ticket_type,
                matched_category=item["matched_category"],
                matched_article_count=len(item["matched_articles"]),
                source_system=case.source_system,
            )
            output_records.append(record)
        except Exception as e:
            errors.append((case.case_id, str(e)))

    return output_records, errors


def validate_pipeline_full(
    output_records: list[JoinedCaseOutput],
    errors: list[tuple],
    contract_name: str = "JoinedCaseOutput",
) -> dict:
    """
    Full validation using the ContractValidator.
    Returns a rich dict with contract check result + build errors.

    This is the new, richer validation API. is_safe_to_deploy() below
    delegates to this and extracts the same (bool, list[str]) shape
    it has always returned, so deploy_gate.py works unchanged.
    """
    reasons = []

    # Build errors (from output record construction)
    if errors:
        reasons.append(f"{len(errors)} record(s) failed output validation — deploy blocked until fixed.")

    # Contract validation
    validator = ContractValidator()
    contract_result = validator.check(output_records, contract_name)

    if not contract_result.is_satisfied:
        for v in contract_result.violations:
            reasons.append(str(v))

    # Warnings (don't block, but are reported)
    warnings = contract_result.warnings

    is_safe = len(reasons) == 0

    return {
        "is_safe": is_safe,
        "reasons": reasons,
        "warnings": warnings,
        "contract_status": contract_result.required_column_status,
        "violation_count": len(contract_result.violations),
    }


def is_safe_to_deploy(output_records: list[JoinedCaseOutput], errors: list[tuple]) -> tuple[bool, list[str]]:
    """
    Defines exactly what 'safe to deploy' means for this pilot.
    Returns (True, []) if safe, otherwise (False, [reasons]).

    Now delegates to validate_pipeline_full() for contract validation,
    but preserves the same (bool, list[str]) return shape so deploy_gate.py
    works unchanged.
    """
    result = validate_pipeline_full(output_records, errors)
    return result["is_safe"], result["reasons"]
