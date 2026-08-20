"""
Composable transform chains with independent preview — FR-NORM-02, FR-NORM-04.

A TransformChain is a sequence of discrete operations (clean, cast, concatenate,
derive) that can be applied to a record. Each step can be previewed independently
without running the full chain.

This compiles to existing IR operators (Cast, Map, Filter) — it does NOT create
a parallel transform system. Each TransformStep maps to an IR MapOperator with
a named transform from the TransformRegistry.

Usage:
    chain = TransformChain(name="ticket_cleaning", steps=[
        TransformStep(name="trim_subject", transform="trim_whitespace", field="subject"),
        TransformStep(name="clean_description", transform="empty_to_none", field="description"),
        TransformStep(name="prefix_id", transform="prefix_id", field="case_id", params={"prefix": "ticket_"}),
    ])
    result = chain.apply(sample_records)
    preview = chain.preview_at_step(sample_records, step_index=1)  # preview after step 1 only
"""

from pydantic import BaseModel, Field
from typing import Any, Optional
from ir.transform_registry import builtin_transform_registry


class TransformStep(BaseModel):
    """
    One discrete operation in a transform chain.

    Each step references a named transform from the TransformRegistry
    (e.g., "trim_whitespace", "empty_to_none", "parse_timestamp", "prefix_id").
    The field specifies which record field to apply the transform to.
    """
    name: str                         # human-readable step name
    transform: str                    # transform name in the registry
    field: str                        # which field to transform
    params: dict = Field(default_factory=dict)  # transform-specific params


class TransformChain(BaseModel):
    """
    A composable sequence of TransformSteps.

    This is the normalization-level abstraction. When compiled to IR,
    each step becomes a MapOperator with the transform name and field params.
    """
    name: str
    steps: list[TransformStep] = Field(default_factory=list)

    def apply(self, records: list[dict]) -> list[dict]:
        """
        Apply the full chain to a list of records (dicts).
        Returns a new list of transformed records — does not mutate input.
        """
        result = [dict(r) for r in records]  # shallow copy each record
        for step in self.steps:
            result = self._apply_step(step, result)
        return result

    def _apply_step(self, step: TransformStep, records: list[dict]) -> list[dict]:
        """Apply a single step to all records."""
        result = []
        for record in records:
            new_record = dict(record)
            current_value = new_record.get(step.field)
            new_record[step.field] = builtin_transform_registry.apply(
                step.transform, current_value, step.params
            )
            result.append(new_record)
        return result

    def preview_at_step(self, records: list[dict], step_index: int) -> list[dict]:
        """
        Preview the output after applying steps 0..step_index (inclusive).
        Does NOT run the remaining steps.

        step_index: 0-based index of the last step to apply.
        Returns the records after steps 0 through step_index.
        """
        if step_index < 0 or step_index >= len(self.steps):
            raise IndexError(
                f"step_index {step_index} out of range (0-{len(self.steps)-1})"
            )
        result = [dict(r) for r in records]
        for i in range(step_index + 1):
            result = self._apply_step(self.steps[i], result)
        return result

    def preview_all_steps(self, records: list[dict]) -> list[dict]:
        """
        Preview the output after EACH individual step.
        Returns a list of dicts, one per step, each containing:
          - step_index
          - step_name
          - records: the records after applying steps 0..step_index
        This is the FR-NORM-04 "preview at each step" feature.
        """
        previews = []
        result = [dict(r) for r in records]
        for i, step in enumerate(self.steps):
            result = self._apply_step(step, result)
            previews.append({
                "step_index": i,
                "step_name": step.name,
                "transform": step.transform,
                "field": step.field,
                "record_count": len(result),
                "records": result,
            })
        return previews

    def to_ir_operators(self, input_dataset: str, output_dataset: str) -> list:
        """
        Compile this chain to a list of IR MapOperators.
        Each step becomes a MapOperator — the chain is a sequence of Map ops
        in the IR, not a new operator type.

        input_dataset: the IR dataset name the chain reads from
        output_dataset: the IR dataset name the final step produces
        """
        from ir.operators import MapOperator

        operators = []
        for i, step in enumerate(self.steps):
            is_last = (i == len(self.steps) - 1)
            op_output = output_dataset if is_last else f"{input_dataset}_step_{i}"
            op_input = input_dataset if i == 0 else f"{input_dataset}_step_{i-1}"
            operators.append(MapOperator(
                op="Map",
                input=op_input,
                output=op_output,
                transform=step.transform,
                params={"field": step.field, **step.params},
                source="manual",
            ))
        return operators

    def summary(self) -> dict:
        return {
            "name": self.name,
            "step_count": len(self.steps),
            "steps": [
                {"index": i, "name": s.name, "transform": s.transform, "field": s.field}
                for i, s in enumerate(self.steps)
            ],
        }
