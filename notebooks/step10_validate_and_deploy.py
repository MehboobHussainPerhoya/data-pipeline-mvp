import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api
from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase
from transform.join_engine import join_cases_to_articles
from transform.join_config import TICKET_TYPE_TO_KB_CATEGORY
from validation.output_validator import build_output_records, is_safe_to_deploy
from validation.deploy_gate import deploy_pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def main():
    # --- Ingest, normalize, join (same as Phase 2) ---
    tickets_raw = read_support_tickets(str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv"))
    kb_raw = read_kb_articles(str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv"))
    api_raw = read_support_activity_api(
        fallback_path=str(PROJECT_ROOT / "data/sample/support_activity_api/sample.json")
    )

    cases = [map_ticket_to_supportcase(r) for r in tickets_raw] + \
            [map_api_to_supportcase(r) for r in api_raw]
    articles = [map_kb_to_knowledgearticle(r, i) for i, r in enumerate(kb_raw)]
    joined = join_cases_to_articles(cases, articles, TICKET_TYPE_TO_KB_CATEGORY)

    # --- Validate ---
    output_records, errors = build_output_records(joined)
    is_safe, reasons = is_safe_to_deploy(output_records, errors)

    print(f"Output records built: {len(output_records)}")
    print(f"Validation errors: {len(errors)}")
    print(f"Safe to deploy: {is_safe}")
    if reasons:
        print(f"Reasons: {reasons}")

    output_path = str(PROJECT_ROOT / "data/processed/support_cases_output.json")

    # --- Prove the gate blocks without approval ---
    print("\nAttempting deploy WITHOUT approval (should be blocked)...")
    try:
        deploy_pipeline(output_records, is_safe, reasons, approved=False, output_path=output_path)
    except RuntimeError as e:
        print(f"Blocked as expected: {e}")

    # --- Prove the gate succeeds WITH approval ---
    print("\nAttempting deploy WITH approval (should succeed)...")
    deploy_pipeline(output_records, is_safe, reasons, approved=True, output_path=output_path)

if __name__ == "__main__":
    main()