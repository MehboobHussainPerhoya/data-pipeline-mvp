"""
IR Executor — runs a PipelineIR against data.

The executor resolves named connectors for ingestion, applies named transforms
for Map operators, and executes Join/Filter/Aggregate/Union/Window operations.

This replaces the imperative logic in run_pipeline() — the pipeline is now
a declarative IR object that the executor interprets.

FSD requirements: FR-IR-02 (engine compilation targets — Python executor for now;
Spark/Flink compilation deferred to later phases)
"""

from typing import Callable, Any
from .pipeline_ir import PipelineIR, InputSource
from .operators import (
    CastOperator, MapOperator, FilterOperator, JoinOperator,
    AggregateOperator, WindowOperator, UnionOperator,
)
from .transform_registry import builtin_transform_registry


class IRExecutor:
    """
    Executes a PipelineIR step by step.

    Holds registries of connector functions and mapper functions that the IR
    references by name. This keeps the IR declarative while the executor holds
    the implementations.

    Usage:
        executor = IRExecutor()
        executor.register_connector("read_support_tickets", read_support_tickets)
        executor.register_mapper("map_ticket_to_supportcase", map_ticket_to_supportcase)
        result = executor.execute(pipeline_ir)
    """

    def __init__(self):
        self._connectors: dict[str, Callable] = {}
        self._mappers: dict[str, Callable] = {}
        self._transforms = builtin_transform_registry

    # --- Registration ---

    def register_connector(self, name: str, func: Callable) -> None:
        """Register a named connector function for ingestion."""
        self._connectors[name] = func

    def register_mapper(self, name: str, func: Callable) -> None:
        """Register a named mapper function for normalization."""
        self._mappers[name] = func

    # --- Execution ---

    def execute(self, ir: PipelineIR, connector_params: dict = None) -> dict:
        """
        Execute the full pipeline IR.

        connector_params: optional dict mapping connector names to kwargs
        (e.g., {"read_support_activity_api": {"fallback_path": "/path/to/sample.json"}})

        Returns a dict of named datasets (the final one being the output).
        """
        connector_params = connector_params or {}
        datasets: dict[str, list[Any]] = {}

        # Step 1: Ingest all input sources
        for source in ir.inputs:
            raw_data = self._ingest(source, connector_params)
            datasets[source.name] = raw_data

        # Step 2: Execute each operator in order
        for i, step in enumerate(ir.steps):
            datasets[step.output] = self._execute_operator(step, datasets)

        return datasets

    def _ingest(self, source: InputSource, connector_params: dict) -> list[dict]:
        """Call the connector function for an input source."""
        connector = self._connectors.get(source.connector)
        if connector is None:
            raise KeyError(f"Connector '{source.connector}' not registered for source '{source.name}'")

        params = connector_params.get(source.connector, {})
        # If the connector expects a file path and the source location looks like a path,
        # pass it as the first positional arg or as file_path kwarg
        if source.source_type == "csv":
            return connector(source.location, **params)
        else:
            return connector(**params)

    def _execute_operator(self, step, datasets: dict) -> list[Any]:
        """Dispatch to the right handler based on operator type."""
        if isinstance(step, CastOperator):
            return self._exec_cast(step, datasets)
        elif isinstance(step, MapOperator):
            return self._exec_map(step, datasets)
        elif isinstance(step, FilterOperator):
            return self._exec_filter(step, datasets)
        elif isinstance(step, JoinOperator):
            return self._exec_join(step, datasets)
        elif isinstance(step, AggregateOperator):
            return self._exec_aggregate(step, datasets)
        elif isinstance(step, UnionOperator):
            return self._exec_union(step, datasets)
        elif isinstance(step, WindowOperator):
            return self._exec_window(step, datasets)
        else:
            raise ValueError(f"Unknown operator type: {type(step).__name__}")

    # --- Individual operator implementations ---

    def _exec_cast(self, step: CastOperator, datasets: dict) -> list[Any]:
        """Cast a field in each record to a target type."""
        data = datasets.get(step.input, [])
        # For now, casting is handled by Pydantic at object construction time.
        # This operator is mainly a declarative marker in the IR — the actual
        # type enforcement happens when records are constructed as Pydantic models.
        # Future: apply explicit cast logic here.
        return list(data)

    def _exec_map(self, step: MapOperator, datasets: dict) -> list[Any]:
        """Apply a named mapper/transform to each record in the input dataset."""
        data = datasets.get(step.input, [])

        # Check if this is a whole-record mapper (e.g., map_ticket_to_supportcase)
        if step.transform in self._mappers:
            mapper = self._mappers[step.transform]
            # Mappers that need a row_index (like kb_mapper) get it via params
            if step.params.get("needs_index"):
                return [mapper(record, i) for i, record in enumerate(data)]
            return [mapper(record) for record in data]

        # Otherwise, it's a field-level transform from the transform registry
        field = step.params.get("field")
        if field:
            result = []
            for record in data:
                new_record = dict(record) if isinstance(record, dict) else record.model_dump()
                current_value = new_record.get(field)
                new_record[field] = self._transforms.apply(step.transform, current_value, step.params)
                result.append(new_record)
            return result

        raise ValueError(f"Map operator transform '{step.transform}' not found in mappers or transforms")

    def _exec_filter(self, step: FilterOperator, datasets: dict) -> list[Any]:
        """Filter records based on a condition expression."""
        data = datasets.get(step.input, [])
        # Simple equality filter: "field == 'value'"
        # Future: proper expression parser
        condition = step.condition
        if "==" in condition:
            field, value = condition.split("==", 1)
            field = field.strip()
            value = value.strip().strip("'\"")
            return [r for r in data if self._get_field(r, field) == value]
        elif "!=" in condition:
            field, value = condition.split("!=", 1)
            field = field.strip()
            value = value.strip().strip("'\"")
            return [r for r in data if self._get_field(r, field) != value]
        else:
            raise ValueError(f"Unsupported filter condition: {condition}")

    def _exec_join(self, step: JoinOperator, datasets: dict) -> list[dict]:
        """Join two datasets on key pairs."""
        left_data = datasets.get(step.left, [])
        right_data = datasets.get(step.right, [])

        # Pre-group right data by join key for efficiency (same optimization
        # as the original join_engine.py)
        for kp in step.on:
            if kp.mapping:
                # Indirect join: group right data by right_key, then look up
                # via mapping[left_val] -> right_val
                right_index: dict[Any, list] = {}
                for right_record in right_data:
                    right_val = self._get_field(right_record, kp.right_key)
                    right_index.setdefault(right_val, []).append(right_record)
                break

        results = []
        for left_record in left_data:
            matched = []
            matched_category = None

            for kp in step.on:
                left_val = self._get_field(left_record, kp.left_key)

                if kp.mapping:
                    # Indirect join via mapping dict
                    matched_category = kp.mapping.get(left_val)
                    matched = right_index.get(matched_category, []) if matched_category else []
                else:
                    # Direct join on equal values
                    for right_record in right_data:
                        right_val = self._get_field(right_record, kp.right_key)
                        if left_val == right_val:
                            matched.append(right_record)

            if step.one_to_many:
                # Produce output compatible with build_output_records:
                # {"case": record, "matched_category": cat, "matched_articles": [...]}
                results.append({
                    "case": left_record,
                    "matched_category": matched_category,
                    "matched_articles": matched,
                })
            else:
                # One-to-one: take first match or None
                results.append({
                    "record": left_record,
                    "matched": matched[0] if matched else None,
                })

        return results

    def _exec_aggregate(self, step: AggregateOperator, datasets: dict) -> list[dict]:
        """Group by fields and apply aggregations."""
        data = datasets.get(step.input, [])

        groups: dict[tuple, list] = {}
        for record in data:
            key = tuple(self._get_field(record, f) for f in step.group_by)
            groups.setdefault(key, []).append(record)

        results = []
        for key, group_records in groups.items():
            result = dict(zip(step.group_by, key))
            for agg in step.agg:
                values = [self._get_field(r, agg.field) for r in group_records if self._get_field(r, agg.field) is not None]
                if agg.fn == "sum":
                    result[agg.as_] = sum(values) if values else 0
                elif agg.fn == "count":
                    result[agg.as_] = len(values)
                elif agg.fn == "avg":
                    result[agg.as_] = sum(values) / len(values) if values else 0
                elif agg.fn == "min":
                    result[agg.as_] = min(values) if values else None
                elif agg.fn == "max":
                    result[agg.as_] = max(values) if values else None
                else:
                    raise ValueError(f"Unsupported aggregation function: {agg.fn}")
            results.append(result)

        return results

    def _exec_union(self, step: UnionOperator, datasets: dict) -> list[Any]:
        """Combine multiple datasets into one."""
        combined = []
        for input_name in step.inputs:
            combined.extend(datasets.get(input_name, []))
        return combined

    def _exec_window(self, step: WindowOperator, datasets: dict) -> list[Any]:
        """Apply a window function. (Basic implementation for row_number.)"""
        data = datasets.get(step.input, [])
        # Simple row_number implementation
        if step.window_func == "row_number":
            # Sort by order_by fields, then assign sequential numbers within partitions
            partitioned: dict[tuple, list] = {}
            for i, record in enumerate(data):
                key = tuple(self._get_field(record, f) for f in step.partition_by)
                partitioned.setdefault(key, []).append((i, record))

            results = []
            for key, items in partitioned.items():
                # Sort by order_by fields
                for field in reversed(step.order_by):
                    items.sort(key=lambda x: self._get_field(x[1], field) or "")
                for row_num, (orig_idx, record) in enumerate(items, 1):
                    new_record = dict(record) if isinstance(record, dict) else record.model_dump()
                    new_record[step.as_] = row_num
                    results.append((orig_idx, new_record))

            results.sort(key=lambda x: x[0])  # restore original order
            return [r for _, r in results]

        raise ValueError(f"Unsupported window function: {step.window_func}")

    # --- Utility ---

    def _get_field(self, record: Any, field: str) -> Any:
        """Get a field value from a record (dict or Pydantic model)."""
        if isinstance(record, dict):
            return record.get(field)
        return getattr(record, field, None)