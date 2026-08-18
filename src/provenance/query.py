"""
LineageQuery - answers "why does this output row/value exist?" by walking
the lineage store and returning the full derivation chain, without
re-running the pipeline (FR-PROV-03).

The query interface wraps a LineageStore and provides human-readable
answers to provenance questions.

FSD requirements:
- FR-PROV-03 (lineage query interface)
"""

from typing import Optional
from .lineage_store import LineageStore, LineageRecord


class LineageQuery:
    """
    Query interface for the lineage store.

    Answers provenance questions without re-running the pipeline:
    - Why does this output field exist? (trace the full derivation chain)
    - What join rule produced this output? (FR-PROV-02)
    - Was this join AI-inferred or manual? If AI, what was the confidence?
    - What source fields contributed to this output field?

    Usage:
        query = LineageQuery(store)
        chain = query.trace_field("customer_lifetime_value", "revenue")
        explanation = query.explain_field("customer_lifetime_value", "revenue")
        joins = query.get_join_provenance()
    """

    def __init__(self, store: LineageStore):
        self._store = store

    def trace_field(self, dataset_name: str, field_name: str) -> list[LineageRecord]:
        """
        Trace the full derivation chain for an output field.

        Returns a list of LineageRecords from most recent derivation
        to original source.
        """
        return self._store.trace_chain(dataset_name, field_name)

    def trace_dataset(self, dataset_name: str) -> list[LineageRecord]:
        """
        Trace the full derivation chain for an entire output dataset.
        """
        return self._store.trace_chain(dataset_name)

    def explain_field(self, dataset_name: str, field_name: str) -> str:
        """
        Return a human-readable explanation of why an output field exists.

        Walks the derivation chain and builds a narrative explanation.
        """
        chain = self.trace_field(dataset_name, field_name)
        if not chain:
            return f"No lineage records found for {dataset_name}.{field_name}"

        lines = [f"Lineage trace for {dataset_name}.{field_name}:"]
        lines.append("")

        for i, record in enumerate(chain):
            indent = "  " * i
            lines.append(f"{indent}Step {record.step_index}: {record.operator_type} operator")
            lines.append(f"{indent}  Output: {record.output_dataset}.{record.output_field or '*'}")
            if record.input_dataset:
                lines.append(f"{indent}  Input: {record.input_dataset} (fields: {', '.join(record.input_fields) if record.input_fields else 'all'})")

            # Join-specific provenance (FR-PROV-02)
            if record.operator_type == "Join":
                lines.append(f"{indent}  Join rule: {record.join_rule}")
                lines.append(f"{indent}  Source: {record.source_origin}")
                if record.confidence is not None:
                    lines.append(f"{indent}  Confidence: {record.confidence}")
                lines.append(f"{indent}  Review status: {record.review_status}")
                if record.rejection_reason:
                    lines.append(f"{indent}  Rejection reason: {record.rejection_reason}")
            elif record.source_origin != "manual":
                lines.append(f"{indent}  Source: {record.source_origin}")
                if record.confidence is not None:
                    lines.append(f"{indent}  Confidence: {record.confidence}")

            if record.operator_params:
                params_str = ", ".join(f"{k}={v}" for k, v in record.operator_params.items() if k != "params")
                if params_str:
                    lines.append(f"{indent}  Parameters: {params_str}")
            lines.append("")

        return "\n".join(lines)

    def get_join_provenance(self) -> list[dict]:
        """
        Return provenance for all join operations (FR-PROV-02).

        For each join, returns: join rule, source (manual/agent/human),
        confidence score, and review status.
        """
        joins = self._store.get_join_lineage()
        results = []
        seen_rules = set()

        for record in joins:
            if record.join_rule and record.join_rule not in seen_rules:
                seen_rules.add(record.join_rule)
                results.append({
                    "join_rule": record.join_rule,
                    "join_type": record.join_type,
                    "source_origin": record.source_origin,
                    "confidence": record.confidence,
                    "review_status": record.review_status,
                    "rejection_reason": record.rejection_reason,
                    "step_index": record.step_index,
                    "output_dataset": record.output_dataset,
                })

        return results

    def get_source_fields(self, dataset_name: str, field_name: str) -> list[str]:
        """
        Return the original source fields that contributed to an output field.

        Walks the derivation chain to the end and collects the input fields
        from the earliest records (the original source data).
        """
        chain = self.trace_field(dataset_name, field_name)
        if not chain:
            return []

        # The last records in the chain are the original sources
        source_fields = []
        for record in reversed(chain):
            if record.input_dataset and not self._store.get_lineage_for_dataset(record.input_dataset):
                # This record's input has no further lineage - it is the original source
                source_fields.extend(record.input_fields)

        return list(dict.fromkeys(source_fields))  # deduplicate, preserve order

    def was_agent_inferred(self, dataset_name: str, field_name: str) -> bool:
        """
        Check if an output field was produced by an agent-inferred operation.
        """
        records = self._store.get_lineage_for_field(dataset_name, field_name)
        return any(r.source_origin == "agent_inferred" for r in records)

    def get_confidence_score(self, dataset_name: str, field_name: str) -> Optional[float]:
        """
        Return the agent confidence score for an output field, if it was
        agent-inferred. Returns None if the field was manually defined.
        """
        records = self._store.get_lineage_for_field(dataset_name, field_name)
        for r in records:
            if r.confidence is not None:
                return r.confidence
        return None

    def summary(self) -> dict:
        """Return a summary of the query interface state."""
        return self._store.summary()
