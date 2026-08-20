"""
Schema-matched union with duplicate detection — FR-UNION-01, FR-UNION-03.

Formalizes the union operation:
  - FR-UNION-01: Unioning sources whose canonical schemas don't match raises
    a pre-execution error, not a silent drop or coercion.
  - FR-UNION-03: Exact-duplicate rows across unioned sources are flagged
    for review, not silently deduplicated.

The existing IR executor's _exec_union() just concatenates lists. This module
provides the pre-execution schema check and duplicate detection that the
executor delegates to.
"""

from pydantic import BaseModel, Field
from typing import Any, Optional
from schema.registry_setup import SCHEMA_REGISTRY, get_schema


class SchemaMismatchError(Exception):
    """Raised when unioning sources whose schemas don't match (FR-UNION-01)."""
    pass


class DuplicateFlag(BaseModel):
    """One flagged duplicate row across unioned sources."""
    record_index: int                    # index in the combined output
    sources: list[str]                   # which sources had this exact row
    field_values: dict                   # the duplicate field values


class UnionResult(BaseModel):
    """Result of a schema-matched union with duplicate detection."""
    records: list[Any] = Field(default_factory=list)
    duplicate_flags: list[DuplicateFlag] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)
    schema_checked: bool = False
    duplicate_count: int = 0

    def summary(self) -> dict:
        return {
            "total_records": len(self.records),
            "duplicate_count": self.duplicate_count,
            "source_counts": self.source_counts,
            "schema_checked": self.schema_checked,
            "duplicates": [
                {
                    "record_index": d.record_index,
                    "sources": d.sources,
                }
                for d in self.duplicate_flags
            ],
        }


def check_schemas_match(schema_names: list[str]) -> None:
    """
    Pre-execution check: verify all sources share the same canonical schema.
    Raises SchemaMismatchError if any source has a different schema.

    FR-UNION-01: mismatches raise a pre-execution error, not a silent drop.
    """
    if len(schema_names) < 2:
        return  # single source — nothing to compare

    first = schema_names[0]
    first_schema = get_schema(first)
    if first_schema is None:
        raise SchemaMismatchError(
            f"Schema '{first}' not found in registry — cannot validate union."
        )

    first_fields = set(f.name for f in first_schema.fields)
    for name in schema_names[1:]:
        schema = get_schema(name)
        if schema is None:
            raise SchemaMismatchError(
                f"Schema '{name}' not found in registry — cannot validate union."
            )
        other_fields = set(f.name for f in schema.fields)
        if other_fields != first_fields:
            only_in_first = first_fields - other_fields
            only_in_other = other_fields - first_fields
            detail_parts = []
            if only_in_first:
                detail_parts.append(f"fields only in {first}: {sorted(only_in_first)}")
            if only_in_other:
                detail_parts.append(f"fields only in {name}: {sorted(only_in_other)}")
            raise SchemaMismatchError(
                f"Schema mismatch: '{first}' and '{name}' have different field sets. "
                + "; ".join(detail_parts)
            )


def _record_to_dict(record: Any) -> dict:
    """Convert a record (Pydantic model or dict) to a dict for comparison."""
    if isinstance(record, dict):
        return record
    if hasattr(record, "model_dump"):
        return record.model_dump()
    return dict(record)


def detect_duplicates(
    records: list[Any],
    source_field: str = "source_system",
) -> list[DuplicateFlag]:
    """
    Detect exact-duplicate rows across unioned sources (FR-UNION-03).
    Two records are duplicates if all their field values are identical
    (excluding the source_system field, which is expected to differ).

    Returns a list of DuplicateFlag objects — does NOT remove the duplicates.
    The records are flagged for review, not silently deduplicated.
    """
    seen: dict[str, list[int]] = {}  # hashable key -> list of indices
    flags = []

    for i, record in enumerate(records):
        d = _record_to_dict(record)
        # Exclude the source field from the comparison — the same data
        # coming from two sources is exactly the scenario we're detecting
        comparison = {k: v for k, v in d.items() if k != source_field}
        key = tuple(sorted(comparison.items(), key=lambda x: x[0]))

        if key in seen:
            seen[key].append(i)
        else:
            seen[key] = [i]

    for key, indices in seen.items():
        if len(indices) > 1:
            # Get the sources involved
            sources = []
            for idx in indices:
                d = _record_to_dict(records[idx])
                sources.append(d.get(source_field, "unknown"))
            flags.append(DuplicateFlag(
                record_index=indices[0],
                sources=sources,
                field_values=dict(key),
            ))

    return flags


def union_with_checks(
    datasets: list[tuple[str, list[Any], str]],
    check_schema: bool = True,
    check_duplicates: bool = True,
) -> UnionResult:
    """
    Union multiple datasets with pre-execution schema check and duplicate detection.

    Each dataset is a tuple of (source_name, records, schema_name).
    All records are combined into one list. If check_schema is True, all
    schema_names must match (FR-UNION-01). If check_duplicates is True,
    exact-duplicate rows are flagged (FR-UNION-03).

    Returns a UnionResult with the combined records and any duplicate flags.
    """
    if check_schema:
        schema_names = [schema for _, _, schema in datasets]
        check_schemas_match(schema_names)

    combined = []
    source_counts = {}
    for source_name, records, _ in datasets:
        combined.extend(records)
        source_counts[source_name] = len(records)

    duplicate_flags = []
    if check_duplicates and len(combined) > 0:
        duplicate_flags = detect_duplicates(combined)

    return UnionResult(
        records=combined,
        duplicate_flags=duplicate_flags,
        source_counts=source_counts,
        schema_checked=check_schema,
        duplicate_count=len(duplicate_flags),
    )
