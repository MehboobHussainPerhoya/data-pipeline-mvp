import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api
from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def main():
    # --- Ingest raw ---
    tickets_raw = read_support_tickets(
        str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv")
    )
    kb_raw = read_kb_articles(
        str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv")
    )
    api_raw = read_support_activity_api()

    # --- Normalize + union SupportCase (tickets + api) ---
    support_cases = []
    errors = []

    for i, row in enumerate(tickets_raw):
        try:
            support_cases.append(map_ticket_to_supportcase(row))
        except Exception as e:
            errors.append(("tickets", i, str(e)))

    for i, row in enumerate(api_raw):
        try:
            support_cases.append(map_api_to_supportcase(row))
        except Exception as e:
            errors.append(("api", i, str(e)))

    # --- Normalize KnowledgeArticle (kb only) ---
    knowledge_articles = []
    for i, row in enumerate(kb_raw):
        try:
            knowledge_articles.append(map_kb_to_knowledgearticle(row, i))
        except Exception as e:
            errors.append(("kb", i, str(e)))

    # --- Report ---
    print(f"SupportCase records (union of tickets + api): {len(support_cases)}")
    print(f"Sample SupportCase: {support_cases[0]}")
    print()
    print(f"KnowledgeArticle records: {len(knowledge_articles)}")
    print(f"Sample KnowledgeArticle: {knowledge_articles[0]}")
    print()
    print(f"Errors encountered: {len(errors)}")
    for err in errors[:10]:  # show first 10 only
        print(f"  {err}")

if __name__ == "__main__":
    main()