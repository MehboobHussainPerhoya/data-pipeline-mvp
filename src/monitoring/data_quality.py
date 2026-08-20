"""
Data-quality metrics — FR-MON-02 (Data-quality metrics).

Computes null-rate, schema-drift, and duplicate-rate per output over time.
These metrics are computed from ACTUAL output records (the cached pipeline
output or files under data/processed/), not from synthetic or hardcoded data.

Schema-drift is computed by comparing the output's actual columns and types
against the Schema Registry (Phase 1) — so "drift" has a real, versioned
baseline to compare against, not an arbitrary snapshot.

This module is OBSERVATIONAL ONLY — it reads output data and the schema
registry. It does not modify output, re-run the pipeline, or deploy.

FSD requirement:
- FR-MON-02: System shall track null-rate, schema-drift, and duplicate-rate
  metrics per output over time.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from schema.registry_setup import get_schema, SCHEMA_REGISTRY
from schema.type_registry import TypeRegistry


@dataclass
class FieldNullRate:
    """Null-rate for a single field."""
    field_name: str
    null_count: int
    total_count: int
    null_rate: float

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "null_count": self.null_count,
            "total_count": self.total_count,
            "null_rate": round(self.null_rate, 4),
        }


@dataclass
class SchemaDriftField:
    """
    Drift finding for a single field.

    drift_type is one of:
    - "missing_field": field in the registered schema but absent from output
    - "extra_field": field in output but not in the registered schema
    - "type_mismatch": field exists in both but the canonical type doesn't match
    """
    field_name: str
    drift_type: str
    expected: str
    actual: str

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "drift_type": self.drift_type,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class DQMetrics:
    """
    Data-quality metrics for one output dataset at one point in time.

    Attributes:
        schema_name: the registered schema name used as the baseline
        timestamp: when these metrics were computed
        record_count: number of records analyzed
        null_rates: per-field null rates
        overall_null_rate: average null rate across all fields
        schema_drift_fields: list of drift findings (empty if no drift)
        has_schema_drift: True if any drift was detected
        duplicate_count: number of exact-duplicate rows found
        duplicate_rate: duplicate_count / record_count
        unique_record_count: number of unique rows
    """
    schema_name: str
    timestamp: str
    record_count: int
    null_rates: list[FieldNullRate] = field(default_factory=list)
    overall_null_rate: float = 0.0
    schema_drift_fields: list[SchemaDriftField] = field(default_factory=list)
    has_schema_drift: bool = False
    duplicate_count: int = 0
    duplicate_rate: float = 0.0
    unique_record_count: int = 0

    def to_dict(self) -> dict:
        return {
            "schema_name": self.schema_name,
            "timestamp": self.timestamp,
            "record_count": self.record_count,
            "null_rates": [nr.to_dict() for nr in self.null_rates],
            "overall_null_rate": round(self.overall_null_rate, 4),
            "schema_drift_fields": [d.to_dict() for d in self.schema_drift_fields],
            "has_schema_drift": self.has_schema_drift,
            "duplicate_count": self.duplicate_count,
            "duplicate_rate": round(self.duplicate_rate, 4),
            "unique_record_count": self.unique_record_count,
        }


def _records_to_dicts(records: list[Any]) -> list[dict]:
    """Convert a list of Pydantic models or dicts to a list of plain dicts."""
    result = []
    for r in records:
        if isinstance(r, dict):
            result.append(r)
        elif hasattr(r, "model_dump"):
            result.append(r.model_dump())
        else:
            result.append(dict(r))
    return result


def _infer_type_name(value: Any) -> str:
    """
    Infer a canonical type name from a Python value.

    Maps Python types to the canonical type names used in the registry.
    Returns "Unknown" for types we can't map.
    """
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Integer"
    if isinstance(value, float):
        return "Double"
    if isinstance(value, str):
        return "String"
    if isinstance(value, datetime):
        return "Timestamp"
    return type(value).__name__


class DataQualityAnalyzer:
    """
    Computes data-quality metrics (null-rate, schema-drift, duplicate-rate)
    from actual output records, using the Schema Registry as the drift
    baseline (FR-MON-02).

    This is a READ-ONLY analyzer — it does not modify output, re-run the
    pipeline, or deploy.

    Usage:
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")
        # metrics.null_rates, metrics.schema_drift_fields, metrics.duplicate_rate
    """

    def __init__(self, registry: TypeRegistry | None = None):
        """
        Args:
            registry: optional TypeRegistry instance for type validation.
                If None, a default one is created.
        """
        self._registry = registry if registry is not None else TypeRegistry()

    def analyze(
        self,
        records: list[Any],
        schema_name: str = "JoinedCaseOutput",
    ) -> DQMetrics:
        """
        Compute all DQ metrics for a set of output records against a
        registered schema.

        Args:
            records: output records (Pydantic models or dicts)
            schema_name: the registered schema name to use as the baseline
                (must exist in SCHEMA_REGISTRY)

        Returns a DQMetrics with null-rate, schema-drift, and duplicate-rate.
        """
        dicts = _records_to_dicts(records)
        timestamp = datetime.now(timezone.utc).isoformat()

        # Get the registered schema as the baseline
        schema = get_schema(schema_name)
        registered_field_names = set(schema.field_names)

        # Determine actual fields present in the output
        actual_field_names: set[str] = set()
        for d in dicts:
            actual_field_names.update(d.keys())

        # --- Null-rate computation ---
        null_rates = self._compute_null_rates(dicts, actual_field_names)
        overall_null_rate = (
            sum(nr.null_rate for nr in null_rates) / len(null_rates)
            if null_rates else 0.0
        )

        # --- Schema-drift computation ---
        drift_fields = self._compute_schema_drift(
            dicts, schema, registered_field_names, actual_field_names
        )

        # --- Duplicate-rate computation ---
        duplicate_count, unique_count = self._compute_duplicates(dicts)
        duplicate_rate = duplicate_count / len(dicts) if dicts else 0.0

        return DQMetrics(
            schema_name=schema_name,
            timestamp=timestamp,
            record_count=len(dicts),
            null_rates=null_rates,
            overall_null_rate=overall_null_rate,
            schema_drift_fields=drift_fields,
            has_schema_drift=len(drift_fields) > 0,
            duplicate_count=duplicate_count,
            duplicate_rate=duplicate_rate,
            unique_record_count=unique_count,
        )

    def _compute_null_rates(
        self,
        dicts: list[dict],
        field_names: set[str],
    ) -> list[FieldNullRate]:
        """Compute per-field null rate across all records."""
        results = []
        total = len(dicts)
        if total == 0:
            return []

        for field_name in sorted(field_names):
            null_count = sum(1 for d in dicts if d.get(field_name) is None)
            rate = null_count / total
            results.append(FieldNullRate(
                field_name=field_name,
                null_count=null_count,
                total_count=total,
                null_rate=rate,
            ))
        return results

    def _compute_schema_drift(
        self,
        dicts: list[dict],
        schema,
        registered_fields: set[str],
        actual_fields: set[str],
    ) -> list[SchemaDriftField]:
        """
        Compute schema drift by comparing actual output fields against the
        registered schema definition.

        Drift types:
        - missing_field: in registered schema but absent from output
        - extra_field: in output but not in registered schema
        - type_mismatch: field exists in both but inferred type doesn't match
          the registered canonical type
        """
        drifts: list[SchemaDriftField] = []

        # Missing fields: in schema but not in output
        for field_name in sorted(registered_fields - actual_fields):
            field_def = schema.get_field(field_name)
            drifts.append(SchemaDriftField(
                field_name=field_name,
                drift_type="missing_field",
                expected=field_def.canonical_type,
                actual="absent",
            ))

        # Extra fields: in output but not in schema
        for field_name in sorted(actual_fields - registered_fields):
            drifts.append(SchemaDriftField(
                field_name=field_name,
                drift_type="extra_field",
                expected="not_in_schema",
                actual="present",
            ))

        # Type mismatch: field in both, but inferred type differs from registered
        for field_name in sorted(registered_fields & actual_fields):
            field_def = schema.get_field(field_name)
            expected_type = field_def.canonical_type

            # Infer the actual type from non-null values in the output
            actual_type = self._infer_dominant_type(dicts, field_name)
            if actual_type == "None":
                # All values are null — can't infer type, skip
                continue

            if not self._types_compatible(expected_type, actual_type):
                drifts.append(SchemaDriftField(
                    field_name=field_name,
                    drift_type="type_mismatch",
                    expected=expected_type,
                    actual=actual_type,
                ))

        return drifts

    def _infer_dominant_type(self, dicts: list[dict], field_name: str) -> str:
        """Infer the dominant non-null type for a field across all records."""
        type_counts: dict[str, int] = {}
        for d in dicts:
            value = d.get(field_name)
            if value is not None:
                t = _infer_type_name(value)
                type_counts[t] = type_counts.get(t, 0) + 1

        if not type_counts:
            return "None"

        # Return the most common type
        return max(type_counts, key=type_counts.get)

    def _types_compatible(self, expected: str, actual: str) -> bool:
        """
        Check if an inferred type is compatible with the expected canonical type.

        Integer and Double are compatible with each other (numeric coercion).
        Everything else must match exactly.
        """
        if expected == actual:
            return True
        # Numeric compatibility: Integer <-> Double
        numeric_types = {"Integer", "Double"}
        if expected in numeric_types and actual in numeric_types:
            return True
        return False

    def _compute_duplicates(self, dicts: list[dict]) -> tuple[int, int]:
        """
        Count exact-duplicate rows.

        Returns (duplicate_count, unique_count) where duplicate_count is the
        number of rows that are exact copies of an earlier row.
        """
        seen: list[str] = []
        duplicate_count = 0

        for d in dicts:
            # Create a hashable representation of the row
            key = repr(sorted(d.items()))
            if key in seen:
                duplicate_count += 1
            else:
                seen.append(key)

        unique_count = len(seen)
        return duplicate_count, unique_count

    def analyze_from_file(
        self,
        file_path: str,
        schema_name: str = "JoinedCaseOutput",
    ) -> DQMetrics:
        """
        Load output records from a JSON file and compute DQ metrics.

        The file should contain a JSON array of record dicts (the format
        written by deploy_gate.py).
        """
        import json
        path = __import__("pathlib").Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Output file not found: {file_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError(f"Expected a JSON array in {file_path}, got {type(data).__name__}")

        return self.analyze(data, schema_name=schema_name)
