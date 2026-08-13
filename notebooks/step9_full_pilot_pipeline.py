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

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def main():
    # --- Ingest ---
    tickets_raw = read_support_tickets(str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv"))
    kb_raw = read_kb_articles(str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv"))
    api_raw = read_support_activity_api(
    fallback_path=str(PROJECT_ROOT / "data/sample/support_activity_api/sample.json")
)

    # --- Normalize ---
    cases = [map_ticket_to_supportcase(r) for r in tickets_raw] + \
            [map_api_to_supportcase(r) for r in api_raw]
    articles = [map_kb_to_knowledgearticle(r, i) for i, r in enumerate(kb_raw)]

    # --- Join ---
    joined = join_cases_to_articles(cases, articles, TICKET_TYPE_TO_KB_CATEGORY)

    # --- Report ---
    print(f"Total cases: {len(joined)}")
    unmatched = [j for j in joined if not j["matched_articles"]]
    print(f"Cases with no matched articles: {len(unmatched)}")

    example = next(j for j in joined if j["matched_articles"])
    print(f"\nExample match:")
    print(f"  Case: {example['case'].case_id} | ticket_type={example['case'].ticket_type} | subject={example['case'].subject}")
    print(f"  Matched category: {example['matched_category']}")
    print(f"  # articles matched: {len(example['matched_articles'])}")
    print(f"  First matched article: {example['matched_articles'][0].question}")

if __name__ == "__main__":
    main()