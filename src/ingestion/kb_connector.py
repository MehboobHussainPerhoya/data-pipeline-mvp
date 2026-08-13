import csv
from pathlib import Path

def read_kb_articles(file_path: str) -> list[dict]:
    """
    Reads the raw kb_articles CSV (Bitext dataset) and returns a list
    of dicts, one per row, keyed by original column names
    (flags, instruction, category, intent, response).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"kb_articles file not found at: {path}")

    records = []
    with open(path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)

    return records