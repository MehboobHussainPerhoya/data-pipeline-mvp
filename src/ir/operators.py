"""
IR Operators — the canonical operator set for the pipeline Intermediate Representation.

Each operator is a Pydantic model: structured, serializable, inspectable.
These are the building blocks the Agent Orchestrator will propose and the
Type Checker will validate.

FSD requirements: FR-IR-01 (canonical operator set)
"""

from pydantic import BaseModel
from typing import Literal, Optional, Any, Union, Annotated


# ---------------------------------------------------------------------------
# Common metadata carried by every operator
# ---------------------------------------------------------------------------

class OperatorBase(BaseModel):
    """Fields common to all IR operators."""
    op: str                          # operator type name ("Cast", "Join", etc.)
    output: str                      # name of the dataset this operator produces
    source: str = "manual"           # who proposed this: "manual", "agent_inferred", "human_override"
    confidence: Optional[float] = None       # 0-1 if agent-inferred, None if manual
    review_status: str = "not_required"      # "approved", "rejected_by_hitl", "pending", "not_required"
    rejection_reason: Optional[str] = None   # why it was rejected, if applicable


# ---------------------------------------------------------------------------
# Individual operator types
# ---------------------------------------------------------------------------

class CastOperator(OperatorBase):
    """Cast a field from its current type to a target canonical type."""
    op: Literal["Cast"] = "Cast"
    input: str               # source dataset name
    field: str               # field to cast
    to: str                  # target canonical type name (from TypeRegistry)


class MapOperator(OperatorBase):
    """Apply a named transform function to each record in a dataset."""
    op: Literal["Map"] = "Map"
    input: str               # source dataset name
    transform: str           # name of the transform function (looked up in TransformRegistry)
    params: dict = {}        # optional transform-specific parameters


class FilterOperator(OperatorBase):
    """Filter rows based on a condition."""
    op: Literal["Filter"] = "Filter"
    input: str               # source dataset name
    condition: str           # filter expression (e.g., "status == 'Open'")


class JoinKeyPair(BaseModel):
    """One key pair in a join condition."""
    left_key: str            # field name in the left dataset
    right_key: str           # field name in the right dataset
    mapping: Optional[dict] = None  # indirect mapping dict (e.g., ticket_type -> category)


class JoinOperator(OperatorBase):
    """Join two datasets on one or more key pairs."""
    op: Literal["Join"] = "Join"
    left: str                # left dataset name
    right: str               # right dataset name
    on: list[JoinKeyPair]    # join key pairs
    type: str = "left"       # "inner", "left", "right", "full"
    one_to_many: bool = False  # if True, each left row gets a list of matching right rows


class AggregationSpec(BaseModel):
    """One aggregation in an Aggregate operator."""
    field: str               # field to aggregate
    fn: str                  # aggregation function: "sum", "count", "avg", "min", "max"
    as_: str                 # output field name for the result


class AggregateOperator(OperatorBase):
    """Group by fields and apply aggregations."""
    op: Literal["Aggregate"] = "Aggregate"
    input: str               # source dataset name
    group_by: list[str]      # fields to group by
    agg: list[AggregationSpec]  # aggregation specifications


class WindowOperator(OperatorBase):
    """Apply a window function over partitioned, ordered rows."""
    op: Literal["Window"] = "Window"
    input: str               # source dataset name
    partition_by: list[str]  # fields to partition by
    order_by: list[str]      # fields to order by
    window_func: str         # function name (e.g., "row_number", "rank", "lag")
    as_: str                 # output field name for the window result


class UnionOperator(OperatorBase):
    """Combine multiple datasets with matching schemas into one."""
    op: Literal["Union"] = "Union"
    inputs: list[str]        # dataset names to union


# ---------------------------------------------------------------------------
# Discriminated union — allows PipelineIR to hold any operator type
# and automatically deserialize the right class based on the "op" field.
# ---------------------------------------------------------------------------

Operator = Annotated[
    Union[
        CastOperator,
        MapOperator,
        FilterOperator,
        JoinOperator,
        AggregateOperator,
        WindowOperator,
        UnionOperator,
    ],
    "op",
]

# Map from op name to class — used for introspection and validation
OPERATOR_TYPES = {
    "Cast": CastOperator,
    "Map": MapOperator,
    "Filter": FilterOperator,
    "Join": JoinOperator,
    "Aggregate": AggregateOperator,
    "Window": WindowOperator,
    "Union": UnionOperator,
}