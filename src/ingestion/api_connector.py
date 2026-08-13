import json
import requests
from pathlib import Path

def read_support_activity_api(
    url: str = "https://jsonplaceholder.typicode.com/todos",
    fallback_path: str = None,
) -> list[dict]:
    """
    Fetches the live support_activity_api feed. If the live call fails
    (e.g. network/DNS issue) and a fallback_path is provided, reads the
    cached sample instead. jsonplaceholder's /todos always returns
    exactly 200 records, so the Phase 0 sample IS the full dataset.
    """
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"Expected a list of records from API, got: {type(data)}")
        return data
    except requests.exceptions.RequestException as e:
        if fallback_path:
            print(f"[WARN] Live API call failed ({e}). Using cached fallback: {fallback_path}")
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        raise ConnectionError(f"Failed to fetch support_activity_api from {url}: {e}")