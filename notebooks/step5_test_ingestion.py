import sys
from pathlib import Path

# Make src/ importable
sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def main():
    print("=== support_tickets ===")
    tickets = read_support_tickets(
        str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv")
    )
    print(f"Rows read: {len(tickets)}")
    print(f"Sample record: {tickets[0]}")

    print("\n=== kb_articles ===")
    kb = read_kb_articles(
        str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv")
    )
    print(f"Rows read: {len(kb)}")
    print(f"Sample record: {kb[0]}")

    print("\n=== support_activity_api ===")
    api_data = read_support_activity_api()
    print(f"Records read: {len(api_data)}")
    print(f"Sample record: {api_data[0]}")

if __name__ == "__main__":
    main()