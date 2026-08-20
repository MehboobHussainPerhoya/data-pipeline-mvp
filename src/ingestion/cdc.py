"""
Incremental/CDC ingestion support — FR-ING-03.

Provides high-water mark tracking so a source isn't fully reprocessed every run.
For CSV sources: tracks the last-seen row index (since CSVs have no native
timestamp/offset). For API sources: tracks the last-seen record ID or timestamp.

This is deliberately simple — no Kafka/CDC log integration, no binlog parsing.
It tracks a high-water mark per source in a JSON file and exposes a method
to filter records to only those newer than the last-seen mark.

Usage:
    tracker = HighWaterMarkTracker(state_path="data/processed/cdc_state.json")
    tracker.update("support_tickets", last_seen_index=5000)
    mark = tracker.get_mark("support_tickets")  # {"last_seen_index": 5000}
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field


class WaterMark(BaseModel):
    """A high-water mark for one source."""
    source_name: str
    last_seen_index: Optional[int] = None       # for CSV/sequential sources
    last_seen_timestamp: Optional[str] = None    # ISO 8601 for timestamp-based sources
    last_seen_id: Optional[Any] = None           # for ID-based sources (API offset)
    last_updated: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_processed: int = 0                     # cumulative count across all runs


class HighWaterMarkTracker:
    """
    Tracks high-water marks per source in a JSON state file.

    The state file is a dict of source_name -> WaterMark dict.
    If the file doesn't exist, all marks start empty (full load).
    """

    def __init__(self, state_path: str = None):
        self.state_path = state_path
        self._marks: dict[str, WaterMark] = {}
        if state_path and Path(state_path).exists():
            self._load()

    def _load(self) -> None:
        """Load marks from the state file."""
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, mark_dict in data.items():
                self._marks[name] = WaterMark(**mark_dict)
        except (json.JSONDecodeError, Exception):
            # Corrupt state file — start fresh (full load)
            self._marks = {}

    def _save(self) -> None:
        """Persist marks to the state file."""
        if not self.state_path:
            return
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        data = {name: mark.model_dump() for name, mark in self._marks.items()}
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_mark(self, source_name: str) -> Optional[WaterMark]:
        """Get the high-water mark for a source, or None if no prior run (full load)."""
        return self._marks.get(source_name)

    def update(
        self,
        source_name: str,
        last_seen_index: int = None,
        last_seen_timestamp: str = None,
        last_seen_id: Any = None,
        records_processed: int = 0,
    ) -> WaterMark:
        """
        Update the high-water mark for a source after a successful incremental pull.

        Only updates fields that are provided (not None). records_processed is
        added to the cumulative total.
        """
        existing = self._marks.get(source_name)
        total = (existing.total_processed if existing else 0) + records_processed

        mark = WaterMark(
            source_name=source_name,
            last_seen_index=last_seen_index if last_seen_index is not None else (existing.last_seen_index if existing else None),
            last_seen_timestamp=last_seen_timestamp if last_seen_timestamp is not None else (existing.last_seen_timestamp if existing else None),
            last_seen_id=last_seen_id if last_seen_id is not None else (existing.last_seen_id if existing else None),
            total_processed=total,
        )
        self._marks[source_name] = mark
        self._save()
        return mark

    def reset(self, source_name: str = None) -> None:
        """
        Reset the mark for one source (or all sources if None).
        The next run will do a full load for the reset source(s).
        """
        if source_name:
            self._marks.pop(source_name, None)
        else:
            self._marks.clear()
        self._save()

    def summary(self) -> dict:
        """Return a summary of all marks."""
        return {
            "state_path": self.state_path,
            "sources_tracked": len(self._marks),
            "marks": {name: mark.model_dump() for name, mark in self._marks.items()},
        }


# ---------------------------------------------------------------------------
# Incremental filtering helpers
# ---------------------------------------------------------------------------

def filter_incremental_csv(
    records: list[dict],
    mark: Optional[WaterMark],
) -> tuple[list[dict], int]:
    """
    Filter CSV records to only those after the last-seen index.
    Returns (filtered_records, new_last_seen_index).

    If mark is None (no prior run), returns all records (full load).
    """
    if mark is None or mark.last_seen_index is None:
        # Full load
        return records, len(records) - 1 if records else 0

    # Incremental: only records after the last-seen index
    start_index = mark.last_seen_index + 1
    if start_index >= len(records):
        return [], mark.last_seen_index  # no new records

    filtered = records[start_index:]
    new_last_seen = len(records) - 1
    return filtered, new_last_seen


def filter_incremental_by_timestamp(
    records: list[dict],
    timestamp_field: str,
    mark: Optional[WaterMark],
) -> tuple[list[dict], Optional[str]]:
    """
    Filter records to only those with a timestamp newer than the last-seen.
    Returns (filtered_records, new_last_seen_timestamp).

    If mark is None (no prior run), returns all records (full load).
    """
    if mark is None or mark.last_seen_timestamp is None:
        # Full load — return all, with the max timestamp
        max_ts = max(
            (r.get(timestamp_field) for r in records if r.get(timestamp_field)),
            default=None,
        )
        return records, max_ts

    last_ts = mark.last_seen_timestamp
    filtered = [r for r in records if r.get(timestamp_field) and r.get(timestamp_field) > last_ts]
    new_max_ts = max(
        (r.get(timestamp_field) for r in filtered if r.get(timestamp_field)),
        default=last_ts,
    )
    return filtered, new_max_ts


def filter_incremental_by_id(
    records: list[dict],
    id_field: str,
    mark: Optional[WaterMark],
) -> tuple[list[dict], Optional[Any]]:
    """
    Filter records to only those with an ID greater than the last-seen ID.
    Returns (filtered_records, new_last_seen_id).

    If mark is None (no prior run), returns all records (full load).
    """
    if mark is None or mark.last_seen_id is None:
        # Full load
        max_id = max(
            (r.get(id_field) for r in records if r.get(id_field) is not None),
            default=None,
        )
        return records, max_id

    last_id = mark.last_seen_id
    filtered = [r for r in records if r.get(id_field) is not None and r.get(id_field) > last_id]
    new_max_id = max(
        (r.get(id_field) for r in filtered if r.get(id_field) is not None),
        default=last_id,
    )
    return filtered, new_max_id
