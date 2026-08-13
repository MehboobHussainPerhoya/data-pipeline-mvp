"""
IR package — Intermediate Representation for the data integration pipeline.

Key exports:
- PipelineIR: the engine-agnostic logical plan
- IRExecutor: runs a PipelineIR against data
- operators: Cast, Map, Filter, Join, Aggregate, Window, Union
- exporter: JSON and pseudocode export
- pipeline_definition: the current MVP pipeline IR
"""

from .operators import (
    CastOperator, MapOperator, FilterOperator, JoinOperator,
    AggregateOperator, WindowOperator, UnionOperator, Operator,
    OPERATOR_TYPES, JoinKeyPair, AggregationSpec,
)
from .pipeline_ir import PipelineIR, InputSource
from .executor import IRExecutor
from .exporter import export_ir_json, export_ir_pseudocode
from .pipeline_definition import support_case_pipeline_ir

__all__ = [
    "PipelineIR",
    "InputSource",
    "IRExecutor",
    "CastOperator", "MapOperator", "FilterOperator", "JoinOperator",
    "AggregateOperator", "WindowOperator", "UnionOperator",
    "Operator", "OPERATOR_TYPES", "JoinKeyPair", "AggregationSpec",
    "export_ir_json", "export_ir_pseudocode",
    "support_case_pipeline_ir",
]