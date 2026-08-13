"""
IR Exporter — human-readable export of the pipeline IR.

Provides two export formats:
1. JSON — for storage, diffing, and machine consumption
2. Pseudocode — for human inspection (the "code escape hatch")

FSD requirements: FR-IR-03 (human-readable export)
"""

from .pipeline_ir import PipelineIR
from .operators import (
    CastOperator, MapOperator, FilterOperator, JoinOperator,
    AggregateOperator, WindowOperator, UnionOperator,
)


def export_ir_json(ir: PipelineIR) -> str:
    """Export the IR as a JSON string."""
    return ir.to_json()


def export_ir_pseudocode(ir: PipelineIR) -> str:
    """
    Export the IR as human-readable pseudocode.
    This is the "code escape hatch" — a user can inspect what the pipeline
    actually does without reading Python.
    """
    lines = []
    lines.append(f"# Pipeline: {ir.name}")
    lines.append(f"# Output contract: {ir.output_contract}")
    lines.append("")

    # Inputs
    lines.append("# Inputs:")
    for src in ir.inputs:
        lines.append(f"  {src.name} = ingest({src.source_type}, '{src.location}', schema={src.schema_name})")
    lines.append("")

    # Steps
    lines.append("# Steps:")
    for i, step in enumerate(ir.steps):
        lines.append(f"  {i}: {format_operator(step)}")
    lines.append("")

    # Output
    if ir.steps:
        last_output = ir.steps[-1].output
        lines.append(f"# Output: {last_output} (validated against {ir.output_contract})")

    return "\n".join(lines)


def format_operator(step) -> str:
    """Format a single operator as pseudocode."""
    meta = f"  [source={step.source}"
    if step.confidence is not None:
        meta += f", confidence={step.confidence}"
    if step.review_status != "not_required":
        meta += f", review={step.review_status}"
    meta += "]"

    if isinstance(step, CastOperator):
        return f"{step.output} = Cast({step.input}.{step.field} -> {step.to}){meta}"

    elif isinstance(step, MapOperator):
        params_str = f", params={step.params}" if step.params else ""
        return f"{step.output} = Map({step.input}, transform={step.transform}{params_str}){meta}"

    elif isinstance(step, FilterOperator):
        return f"{step.output} = Filter({step.input}, where {step.condition}){meta}"

    elif isinstance(step, JoinOperator):
        keys = ", ".join(
            f"{kp.left_key}={kp.right_key}" + (f" via {kp.mapping}" if kp.mapping else "")
            for kp in step.on
        )
        return f"{step.output} = Join({step.left}, {step.right}, on [{keys}], type={step.type}){meta}"

    elif isinstance(step, AggregateOperator):
        aggs = ", ".join(f"{a.fn}({a.field}) as {a.as_}" for a in step.agg)
        return f"{step.output} = Aggregate({step.input}, group_by={step.group_by}, {aggs}){meta}"

    elif isinstance(step, UnionOperator):
        return f"{step.output} = Union({', '.join(step.inputs)}){meta}"

    elif isinstance(step, WindowOperator):
        return f"{step.output} = Window({step.input}, partition_by={step.partition_by}, order_by={step.order_by}, {step.window_func} as {step.as_}){meta}"

    else:
        return f"{step.output} = {step.op}({step.input}){meta}"