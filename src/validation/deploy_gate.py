import json
from datetime import datetime
from pathlib import Path
from schema.output_schema import JoinedCaseOutput

def deploy_pipeline(
    output_records: list[JoinedCaseOutput],
    is_safe: bool,
    safety_reasons: list[str],
    approved: bool,
    output_path: str,
) -> None:
    """
    The single choke point all deployment must pass through.
    Blocks if not safe OR not explicitly approved — no exceptions, no bypass flag.
    This function's shape (safety check + explicit approval + audit log) is
    what becomes the MCP 'deploy' tool in Phase 4 — write it exactly as
    strict as it needs to be permanently, since that's what carries forward.
    """
    if not is_safe:
        raise RuntimeError(f"Deploy blocked — output failed safety checks: {safety_reasons}")

    if not approved:
        raise RuntimeError("Deploy blocked — explicit human approval was not given (approved=False).")

    # Write the deployable output
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([r.model_dump() for r in output_records], f, indent=2)

    # Audit log — every deploy must be traceable
    audit_path = path.parent / "deploy_audit_log.txt"
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} | Deployed {len(output_records)} records to {output_path} | approved=True\n")

    print(f"Deployed {len(output_records)} records to {output_path}")