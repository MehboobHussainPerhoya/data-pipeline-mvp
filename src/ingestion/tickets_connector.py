import csv
from pathlib import Path

def read_support_tickets(file_path: str) -> list[dict]:
    """
    Reads the raw support_tickets CSV and returns a list of dicts,
    one per row, keyed by the original CSV column names.
    No transformation — just faithful reading.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"support_tickets file not found at: {path}")

    records = []
    with open(path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)

    return records