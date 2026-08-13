import sys
from pathlib import Path
from collections import Counter

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api
from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def check_duplicates(records, id_field_getter, label):
    ids = [id_field_getter(r) for r in records]
    counts = Counter(ids)
    dupes = {k: v for k, v in counts.items() if v > 1}
    print(f"[{label}] Duplicate IDs: {len(dupes)}")
    if dupes:
        sample = list(dupes.items())[:5]
        print(f"  Examples: {sample}")

def check_empty_field(records, field_getter, field_name, label):
    empty_count = sum(1 for r in records if field_getter(r) is None or field_getter(r) == "")
    pct = (empty_count / len(records)) * 100 if records else 0
    print(f"[{label}] '{field_name}' empty in {empty_count}/{len(records)} records ({pct:.1f}%)")

def main():
    tickets_raw = read_support_tickets(str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv"))
    kb_raw = read_kb_articles(str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv"))
    api_raw = read_support_activity_api()

    support_cases = [map_ticket_to_supportcase(r) for r in tickets_raw] + \
                     [map_api_to_supportcase(r) for r in api_raw]
    knowledge_articles = [map_kb_to_knowledgearticle(r, i) for i, r in enumerate(kb_raw)]

    print("=== SupportCase ===")
    check_duplicates(support_cases, lambda r: r.case_id, "SupportCase")
    check_empty_field(support_cases, lambda r: r.status, "status", "SupportCase")
    check_empty_field(support_cases, lambda r: r.resolution, "resolution", "SupportCase")

    print("\n=== KnowledgeArticle ===")
    check_duplicates(knowledge_articles, lambda r: r.article_id, "KnowledgeArticle")
    check_empty_field(knowledge_articles, lambda r: r.category, "category", "KnowledgeArticle")

if __name__ == "__main__":
    main()