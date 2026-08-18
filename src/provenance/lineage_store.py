"""
LineageStore - queryable in-memory structure holding recorded derivations.

Every derivation records: input field(s) -> output field(s) -> operator + parameters.
For joins, additional metadata records the join rule and (if agent-inferred)
the confidence score and review status from the Proposal that produced it.

This is in-memory for the MVP. A production system would persist this to a
graph database or relational store. The interface is designed so persistence
could be added later without changing the query API.

FSD requirements:
- FR-PROV-01 (field-level lineage - source fields + transform chain)
- FR-PROV-02 (row-level join provenance - join rule + agent decision + confidence)
"""

from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime, timezone


class LineageRecord(BaseModel):
    """
    One derivation record: how an output field/row came to exist.

    Attributes:
        record_id: stable identifier for this lineage record
        step_index: position in the pipeline IR (which operator produced this)
        operator_type: Cast, Map, Filter, Join, Aggregate, Window, Union
        output_dataset: name of the dataset this operator produced
        output_field: specific field produced (None for whole-record operators)
        input_dataset: source dataset name
        input_fields: list of source field(s) this output derives from
        operator_params: dict of operator-specific parameters
        timestamp: when this record was created

        Join-specific (FR-PROV-02):
        join_rule: for Join operators, a description of the join condition
        join_type: inner, left, right, full
        source_origin: manual, agent_inferred, human_override
        confidence: agent confidence score if agent_inferred, else None
        review_status: approved, rejected_by_hitl, pending, not_required
        rejection_reason: if the join was rejected by HITL, why
        proposal_evidence: the evidence dict from the agent Proposal (if applicable)
    """
    record_id: str
    step_index: int
    operator_type: str
    output_dataset: str
    output_field: Optional[str] = None
    input_dataset: str = ""
    input_fields: list[str] = Field(default_factory=list)
    operator_params: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Join-specific provenance (FR-PROV-02)
    join_rule: Optional[str] = None
    join_type: Optional[str] = None
    source_origin: str = "manual"
    confidence: Optional[float] = None
    review_status: str = "not_required"
    rejection_reason: Optional[str] = None
    proposal_evidence: dict = Field(default_factory=dict)

    def summary(self) -> dict:
        """Return a dict summary suitable for display / MCP output."""
        return self.model_dump()


class LineageStore:
    """
    Queryable in-memory store of lineage records.

    Holds all LineageRecords produced during a pipeline execution.
    Supports queries by output dataset, output field, operator type,
    and full-chain traversal from a final output back to its sources.
    """

    def __init__(self):
        self._records: list[LineageRecord] = []
        self._by_output_dataset: dict[str, list[int]] = {}
        self._by_output_field: dict[str, list[int]] = {}
        self._by_operator: dict[str, list[int]] = {}

    def add(self, record: LineageRecord) -> None:
        """Add a lineage record to the store and update indices."""
        idx = len(self._records)
        self._records.append(record)
        self._by_output_dataset.setdefault(record.output_dataset, []).append(idx)
        if record.output_field:
            key = f"{record.output_dataset}.{record.output_field}"
            self._by_output_field.setdefault(key, []).append(idx)
        self._by_operator.setdefault(record.operator_type, []).append(idx)

    def get_all(self) -> list[LineageRecord]:
        """Return all lineage records."""
        return list(self._records)

    def get_lineage_for_dataset(self, dataset_name: str) -> list[LineageRecord]:
        """Return all lineage records for a given output dataset."""
        indices = self._by_output_dataset.get(dataset_name, [])
        return [self._records[i] for i in indices]

    def get_lineage_for_field(self, dataset_name: str, field_name: str) -> list[LineageRecord]:
        """Return lineage records for a specific output field."""
        key = f"{dataset_name}.{field_name}"
        indices = self._by_output_field.get(key, [])
        return [self._records[i] for i in indices]

    def get_lineage_by_operator(self, operator_type: str) -> list[LineageRecord]:
        """Return all lineage records produced by a given operator type."""
        indices = self._by_operator.get(operator_type, [])
        return [self._records[i] for i in indices]

    def get_join_lineage(self) -> list[LineageRecord]:
        """Return all join lineage records (FR-PROV-02)."""
        return self.get_lineage_by_operator("Join")

    def trace_chain(self, dataset_name: str, field_name: str = None) -> list[LineageRecord]:
        """
        Trace the full derivation chain for an output field or dataset.

        Walks backward from the output to its inputs, then from each input
        to its inputs, until reaching the original source data.

        Returns a list of LineageRecords: most recent derivation first,
        original source last.
        """
        chain: list[LineageRecord] = []
        visited: set[str] = set()
        self._trace_recursive(dataset_name, field_name, chain, visited)
        return chain

    def _trace_recursive(
        self,
        dataset_name: str,
        field_name: Optional[str],
        chain: list[LineageRecord],
        visited: set[str],
    ) -> None:
        """Recursively trace the derivation chain."""
        key = f"{dataset_name}.{field_name or '*'}"
        if key in visited:
            return
        visited.add(key)

        if field_name:
            records = self.get_lineage_for_field(dataset_name, field_name)
        else:
            records = self.get_lineage_for_dataset(dataset_name)

        for record in records:
            chain.append(record)
            if record.input_dataset:
                self._trace_recursive(record.input_dataset, None, chain, visited)

    def summary(self) -> dict:
        """Return a summary of the store contents."""
        return {
            "total_records": len(self._records),
            "by_operator": {op: len(idxs) for op, idxs in self._by_operator.items()},
            "datasets_tracked": list(self._by_output_dataset.keys()),
            "join_records": len(self.get_join_lineage()),
        }

    def to_dict_list(self) -> list[dict]:
        """Serialize all records to a list of dicts (for JSON export / MCP)."""
        return [r.model_dump() for r in self._records]
