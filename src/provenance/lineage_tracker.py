"""
LineageTracker - hooks into the IR executor to record derivations.

For every operator execution, records:
- input field(s) -> output field(s) -> operator + parameters (FR-PROV-01)
- For Join operators: which join rule was used, and if the join came from an
  agent Proposal, which proposal, its confidence score, and review status
  (FR-PROV-02). This data is pulled directly from the IR operator's own
  source/confidence/review_status fields - NOT re-derived separately.

The tracker wraps the IRExecutor. After each operator runs, it inspects
the operator and the input/output data to build a LineageRecord.

FSD requirements:
- FR-PROV-01 (field-level lineage)
- FR-PROV-02 (row-level join provenance)
"""

from typing import Any, Optional
from .lineage_store import LineageStore, LineageRecord
from ir.operators import (
    CastOperator, MapOperator, FilterOperator, JoinOperator,
    AggregateOperator, WindowOperator, UnionOperator,
)


class LineageTracker:
    """
    Records lineage for every operator execution in a pipeline run.

    Wraps an IRExecutor. After each operator runs, build a LineageRecord
    from the operator metadata and the actual input/output data, then add
    it to the LineageStore.

    Usage:
        tracker = LineageTracker()
        # After executing operator at step_index with input/output data:
        tracker.record_execution(step, step_index, input_data, output_data)
        # Query the store:
        records = tracker.store.get_all()
    """

    def __init__(self):
        self.store = LineageStore()
        self._record_counter = 0

    def record_execution(
        self,
        step: Any,
        step_index: int,
        datasets_before: dict[str, list[Any]],
        output_data: list[Any],
    ) -> None:
        """
        Record lineage for one operator execution.

        Parameters:
            step: the IR operator (CastOperator, JoinOperator, etc.)
            step_index: position in the pipeline
            datasets_before: the datasets dict BEFORE this operator ran
                            (so we can see what inputs were available)
            output_data: the output list this operator produced
        """
        self._record_counter += 1
        record_id = f"lineage_{self._record_counter:04d}"

        if isinstance(step, CastOperator):
            self._record_cast(step, step_index, record_id)
        elif isinstance(step, MapOperator):
            self._record_map(step, step_index, record_id, datasets_before, output_data)
        elif isinstance(step, FilterOperator):
            self._record_filter(step, step_index, record_id, datasets_before, output_data)
        elif isinstance(step, JoinOperator):
            self._record_join(step, step_index, record_id, datasets_before, output_data)
        elif isinstance(step, AggregateOperator):
            self._record_aggregate(step, step_index, record_id, datasets_before, output_data)
        elif isinstance(step, UnionOperator):
            self._record_union(step, step_index, record_id)
        elif isinstance(step, WindowOperator):
            self._record_window(step, step_index, record_id)

    def _get_fields(self, data: list[Any]) -> list[str]:
        """Extract field names from the first record in a dataset."""
        if not data:
            return []
        first = data[0]
        if isinstance(first, dict):
            return list(first.keys())
        if hasattr(first, "model_dump"):
            return list(first.model_dump().keys())
        return []

    def _record_cast(self, step: CastOperator, step_index: int, record_id: str) -> None:
        """Record lineage for a Cast operator (FR-PROV-01)."""
        self.store.add(LineageRecord(
            record_id=record_id,
            step_index=step_index,
            operator_type="Cast",
            output_dataset=step.output,
            output_field=step.field,
            input_dataset=step.input,
            input_fields=[step.field],
            operator_params={"to": step.to},
            source_origin=step.source,
            confidence=step.confidence,
            review_status=step.review_status,
        ))

    def _record_map(
        self,
        step: MapOperator,
        step_index: int,
        record_id: str,
        datasets_before: dict[str, list[Any]],
        output_data: list[Any],
    ) -> None:
        """Record lineage for a Map operator (FR-PROV-01)."""
        input_fields = self._get_fields(datasets_before.get(step.input, []))
        output_fields = self._get_fields(output_data)

        # Record one lineage entry per output field that differs from input,
        # or one whole-record entry if the transform is a whole-record mapper
        field = step.params.get("field")
        if field:
            # Field-level transform
            self.store.add(LineageRecord(
                record_id=record_id,
                step_index=step_index,
                operator_type="Map",
                output_dataset=step.output,
                output_field=field,
                input_dataset=step.input,
                input_fields=[field],
                operator_params={"transform": step.transform, "params": step.params},
                source_origin=step.source,
                confidence=step.confidence,
                review_status=step.review_status,
            ))
        else:
            # Whole-record mapper (e.g., map_ticket_to_supportcase)
            # Record lineage for each output field, tracing back to all input fields
            for out_field in output_fields:
                self._record_counter += 1
                rid = f"lineage_{self._record_counter:04d}"
                self.store.add(LineageRecord(
                    record_id=rid,
                    step_index=step_index,
                    operator_type="Map",
                    output_dataset=step.output,
                    output_field=out_field,
                    input_dataset=step.input,
                    input_fields=input_fields,
                    operator_params={"transform": step.transform},
                    source_origin=step.source,
                    confidence=step.confidence,
                    review_status=step.review_status,
                ))

    def _record_filter(
        self,
        step: FilterOperator,
        step_index: int,
        record_id: str,
        datasets_before: dict[str, list[Any]],
        output_data: list[Any],
    ) -> None:
        """Record lineage for a Filter operator (FR-PROV-01)."""
        input_fields = self._get_fields(datasets_before.get(step.input, []))
        output_fields = self._get_fields(output_data)

        for out_field in output_fields:
            self._record_counter += 1
            rid = f"lineage_{self._record_counter:04d}"
            self.store.add(LineageRecord(
                record_id=rid,
                step_index=step_index,
                operator_type="Filter",
                output_dataset=step.output,
                output_field=out_field,
                input_dataset=step.input,
                input_fields=input_fields,
                operator_params={"condition": step.condition},
                source_origin=step.source,
                confidence=step.confidence,
                review_status=step.review_status,
            ))

    def _record_join(
        self,
        step: JoinOperator,
        step_index: int,
        record_id: str,
        datasets_before: dict[str, list[Any]],
        output_data: list[Any],
    ) -> None:
        """
        Record lineage for a Join operator (FR-PROV-01 + FR-PROV-02).

        FR-PROV-02: For joined rows, record which join rule was used, and
        if AI-inferred, which agent decision and confidence score produced
        the match. This data comes directly from the operator's own fields:
        - step.source: "manual", "agent_inferred", "human_override"
        - step.confidence: the agent's confidence score
        - step.review_status: approved, rejected_by_hitl, pending, etc.
        - step.rejection_reason: if rejected, why

        We do NOT re-derive this from the Proposal - it is already on the
        operator because JoinInference sets it when creating the JoinOperator.
        """
        # Build a human-readable description of the join rule
        join_parts = []
        for kp in step.on:
            if kp.mapping:
                join_parts.append(
                    f"{kp.left_key} -> mapping -> {kp.right_key}"
                )
            else:
                join_parts.append(
                    f"{kp.left_key} == {kp.right_key}"
                )
        join_rule = f"{step.left} JOIN {step.right} ON {', '.join(join_parts)} (type={step.type})"

        # Get fields from both sides
        left_fields = self._get_fields(datasets_before.get(step.left, []))
        right_fields = self._get_fields(datasets_before.get(step.right, []))
        output_fields = self._get_fields(output_data)
        all_input_fields = left_fields + right_fields

        # Record one lineage entry per output field
        for out_field in output_fields:
            self._record_counter += 1
            rid = f"lineage_{self._record_counter:04d}"
            self.store.add(LineageRecord(
                record_id=rid,
                step_index=step_index,
                operator_type="Join",
                output_dataset=step.output,
                output_field=out_field,
                input_dataset=f"{step.left} + {step.right}",
                input_fields=all_input_fields,
                operator_params={
                    "left": step.left,
                    "right": step.right,
                    "on": [{"left_key": kp.left_key, "right_key": kp.right_key, "has_mapping": kp.mapping is not None} for kp in step.on],
                    "type": step.type,
                    "one_to_many": step.one_to_many,
                },
                # FR-PROV-02: join-specific provenance from the operator itself
                join_rule=join_rule,
                join_type=step.type,
                source_origin=step.source,
                confidence=step.confidence,
                review_status=step.review_status,
                rejection_reason=step.rejection_reason,
                # proposal_evidence would come from the Proposal; the operator
                # carries source/confidence/review_status which is what we need
            ))

    def _record_aggregate(
        self,
        step: AggregateOperator,
        step_index: int,
        record_id: str,
        datasets_before: dict[str, list[Any]],
        output_data: list[Any],
    ) -> None:
        """Record lineage for an Aggregate operator (FR-PROV-01)."""
        input_fields = self._get_fields(datasets_before.get(step.input, []))

        # Record lineage for each aggregation output field
        for agg in step.agg:
            self._record_counter += 1
            rid = f"lineage_{self._record_counter:04d}"
            self.store.add(LineageRecord(
                record_id=rid,
                step_index=step_index,
                operator_type="Aggregate",
                output_dataset=step.output,
                output_field=agg.as_,
                input_dataset=step.input,
                input_fields=[agg.field] + step.group_by,
                operator_params={
                    "group_by": step.group_by,
                    "fn": agg.fn,
                    "field": agg.field,
                },
                source_origin=step.source,
                confidence=step.confidence,
                review_status=step.review_status,
            ))

        # Also record the group_by fields (they pass through unchanged)
        for gb_field in step.group_by:
            self._record_counter += 1
            rid = f"lineage_{self._record_counter:04d}"
            self.store.add(LineageRecord(
                record_id=rid,
                step_index=step_index,
                operator_type="Aggregate",
                output_dataset=step.output,
                output_field=gb_field,
                input_dataset=step.input,
                input_fields=[gb_field],
                operator_params={"group_by_passthrough": True},
                source_origin=step.source,
                confidence=step.confidence,
                review_status=step.review_status,
            ))

    def _record_union(self, step: UnionOperator, step_index: int, record_id: str) -> None:
        """Record lineage for a Union operator (FR-PROV-01)."""
        for input_name in step.inputs:
            self._record_counter += 1
            rid = f"lineage_{self._record_counter:04d}"
            self.store.add(LineageRecord(
                record_id=rid,
                step_index=step_index,
                operator_type="Union",
                output_dataset=step.output,
                input_dataset=input_name,
                input_fields=[],
                operator_params={"inputs": step.inputs},
                source_origin=step.source,
                confidence=step.confidence,
                review_status=step.review_status,
            ))

    def _record_window(self, step: WindowOperator, step_index: int, record_id: str) -> None:
        """Record lineage for a Window operator (FR-PROV-01)."""
        self.store.add(LineageRecord(
            record_id=record_id,
            step_index=step_index,
            operator_type="Window",
            output_dataset=step.output,
            output_field=step.as_,
            input_dataset=step.input,
            input_fields=step.partition_by + step.order_by,
            operator_params={
                "partition_by": step.partition_by,
                "order_by": step.order_by,
                "window_func": step.window_func,
            },
            source_origin=step.source,
            confidence=step.confidence,
            review_status=step.review_status,
        ))

    def get_store(self) -> LineageStore:
        """Return the lineage store for querying."""
        return self.store

    def summary(self) -> dict:
        """Return a summary of the tracker state."""
        return self.store.summary()
