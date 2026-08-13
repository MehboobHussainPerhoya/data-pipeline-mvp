import sys
from pathlib import Path
from collections import Counter

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def main():
    tickets_raw = read_support_tickets(
        str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv")
    )
    kb_raw = read_kb_articles(
        str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv")
    )

    print("=== support_tickets: 'Ticket Type' distinct values ===")
    ticket_types = Counter(r["Ticket Type"] for r in tickets_raw)
    for val, count in ticket_types.most_common():
        print(f"  {val}: {count}")

    print("\n=== kb_articles: 'category' distinct values ===")
    categories = Counter(r["category"] for r in kb_raw)
    for val, count in categories.most_common():
        print(f"  {val}: {count}")

    print("\n=== kb_articles: 'intent' distinct values ===")
    intents = Counter(r["intent"] for r in kb_raw)
    for val, count in intents.most_common(30):  # intent likely has many values
        print(f"  {val}: {count}")

if __name__ == "__main__":
    main()