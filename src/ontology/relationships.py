"""
Relationships — FR-ONT-02.

A Relationship is a semantic link between two business object types,
derived from a Join operator that already exists in the pipeline IR.
Relationships are NOT invented separately — every relationship must trace
to a concrete JoinOperator with concrete join key pairs.

This is the critical FR-ONT-02 requirement: the relationship documents which
join key actually produced the link. For the CLV case, this means the
relationship is Transaction --[product_id -> product_variation_id]--> Product,
NOT Transaction --[product_id -> product_id]--> Product, because the pipeline's
corrected join (FSD Section 7, step 8) uses product_variation_id as the right key.

FSD requirements: FR-ONT-02 (relationship/link definition)
"""

from dataclasses import dataclass, field
from typing import Optional, Any
from ir.operators import JoinOperator, JoinKeyPair, MapOperator, UnionOperator
from ir.pipeline_ir import PipelineIR
from .object_types import BusinessObjectType, OBJECT_TYPE_REGISTRY, get_object_type


@dataclass
class RelationshipKey:
    """
    One key pair in a relationship — directly from a JoinKeyPair in the IR.

n    Attributes:
        left_key: the field in the left object type
        right_key: the field in the right object type
        has_mapping: True if this is an indirect join (e.g., ticket_type -> category)
        mapping: the mapping dict if indirect, None if direct equality
    """
    left_key: str
    right_key: str
    has_mapping: bool = False
    mapping: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "left_key": self.left_key,
            "right_key": self.right_key,
            "has_mapping": self.has_mapping,
            "mapping": self.mapping if self.has_mapping else None,
        }


@dataclass
class Relationship:
    """
    A semantic link between two business object types, derived from a real
    Join operator in the pipeline IR.

n    Attributes:
        name: human-readable name (e.g., "TransactionToProduct")
        left_object_type: the source object type (e.g., Transaction)
        right_object_type: the target object type (e.g., Product)
        keys: the join key pair(s) that produce this link — directly from the IR
        join_type: inner, left, right, full — from the Join operator
        source_origin: manual, agent_inferred, human_override — from the operator
        confidence: agent confidence score if agent_inferred, else None
        review_status: approved, rejected_by_hitl, pending, etc. — from the operator
        rejection_reason: if the join was rejected, why
        source_ir_step_index: which step in the pipeline IR this relationship came from
        source_ir_output: the output dataset name from the Join operator

    The keys field is the critical part: it records the EXACT join key pair
    from the pipeline. For the CLV case, keys[0].right_key will be
    "product_variation_id", not "product_id" — because that's what the
    corrected join in the IR actually uses.
    """
    name: str
    left_object_type: BusinessObjectType
    right_object_type: BusinessObjectType
    keys: list[RelationshipKey]
    join_type: str = "left"
    source_origin: str = "manual"
    confidence: Optional[float] = None
    review_status: str = "not_required"
    rejection_reason: Optional[str] = None
    source_ir_step_index: int = -1
    source_ir_output: str = ""

    def to_dict(self) -> dict:
        """Serialize for MCP tool output / inspection."""
        return {
            "name": self.name,
            "left_object_type": self.left_object_type.name,
            "right_object_type": self.right_object_type.name,
            "keys": [k.to_dict() for k in self.keys],
            "join_type": self.join_type,
            "source_origin": self.source_origin,
            "confidence": self.confidence,
            "review_status": self.review_status,
            "rejection_reason": self.rejection_reason,
            "source_ir_step_index": self.source_ir_step_index,
            "source_ir_output": self.source_ir_output,
        }

    def describe(self) -> str:
        """
        Human-readable description of this relationship.

n        Explicitly names the join key, so it's clear which key produced the link.
        """
        key_parts = []
        for k in self.keys:
            if k.has_mapping:
                key_parts.append(f"{k.left_key} ->mapping-> {k.right_key}")
            else:
                key_parts.append(f"{k.left_key} -> {k.right_key}")
        keys_str = ", ".join(key_parts)
        return (
            f"{self.left_object_type.name} --[{keys_str}]--> {self.right_object_type.name} "
            f"(type={self.join_type}, source={self.source_origin})"
        )


def _resolve_object_type(dataset_name: str, ir: PipelineIR) -> Optional[BusinessObjectType]:
    """
    Resolve a dataset name from the IR to a BusinessObjectType.

n    The IR's InputSource declarations carry a schema_name. We match that
    to an object type in OBJECT_TYPE_REGISTRY. We also trace through Map,
    Union, and Join operators to resolve intermediate dataset names:
    - "all_cases" is the output of a Union of "tickets_normalized" and
      "api_normalized", which are Map outputs of input sources with
      schema_name="SupportCase".
    - "joined_clv" is the output of a Join of "transactions" and "products".
      A join's output inherits the left side's schema, because a left join
      preserves the left side's row identity — each output row corresponds
      to exactly one left row, enriched with right-side data. This is the
      correct semantic per FSD 4.12: the relationship between object types
      is about the link, not the intermediate dataset's type.

n    This tracing handles arbitrary operator chaining (Join-into-Join,
    Join-into-Union, Join-into-Map, etc.) because steps are processed in
    order, so each operator's output schema is available for any subsequent
    operator that references it.

n    If no match is found, return None (not all datasets map to business
    object types).
    """
    # Build a map of dataset_name -> schema_name by tracing the IR
    dataset_schemas: dict[str, str] = {}

    # Input sources map directly to their schema_name
    for source in ir.inputs:
        dataset_schemas[source.name] = source.schema_name

    # Trace through operators to find output dataset schemas
    for step in ir.steps:
        if isinstance(step, MapOperator):
            # Map output inherits the schema of its input (it's a normalization)
            input_schema = dataset_schemas.get(step.input)
            if input_schema:
                dataset_schemas[step.output] = input_schema
        elif isinstance(step, UnionOperator):
            # Union output inherits the schema of its first input
            # (all inputs must have matching schemas per FR-UNION-01)
            for inp in step.inputs:
                input_schema = dataset_schemas.get(inp)
                if input_schema:
                    dataset_schemas[step.output] = input_schema
                    break
        elif isinstance(step, JoinOperator):
            # Join output inherits the schema of its left input.
            # A left join preserves the left side's row identity — each
            # output row corresponds to one left row, enriched with right
            # data. The output's primary business object type is therefore
            # the left type. This is correct per FSD 4.12: relationships
            # are links between object types, and a chained join's output
            # inherits the object type of its own left input (recursively,
            # if joins are chained more than two deep — handled naturally
            # because we process steps in order).
            left_schema = dataset_schemas.get(step.left)
            if left_schema:
                dataset_schemas[step.output] = left_schema

    # Now resolve the dataset name to an object type
    schema_name = dataset_schemas.get(dataset_name)
    if schema_name and schema_name in OBJECT_TYPE_REGISTRY:
        return OBJECT_TYPE_REGISTRY[schema_name]

    # Fallback: check if the dataset name itself matches an object type name
    if dataset_name in OBJECT_TYPE_REGISTRY:
        return OBJECT_TYPE_REGISTRY[dataset_name]

    return None


def _derive_relationship_from_join(
    join_op: JoinOperator,
    step_index: int,
    ir: PipelineIR,
) -> Optional[Relationship]:
    """
    Derive a Relationship from a single JoinOperator in the IR.

n    Returns None if the join's datasets can't be resolved to object types.
    """
    left_type = _resolve_object_type(join_op.left, ir)
    right_type = _resolve_object_type(join_op.right, ir)

    if left_type is None or right_type is None:
        return None

    # Build RelationshipKey list directly from the JoinKeyPairs
    keys = []
    for kp in join_op.on:
        keys.append(RelationshipKey(
            left_key=kp.left_key,
            right_key=kp.right_key,
            has_mapping=kp.mapping is not None,
            mapping=kp.mapping,
        ))

    name = f"{left_type.name}To{right_type.name}"

    return Relationship(
        name=name,
        left_object_type=left_type,
        right_object_type=right_type,
        keys=keys,
        join_type=join_op.type,
        source_origin=join_op.source,
        confidence=join_op.confidence,
        review_status=join_op.review_status,
        rejection_reason=join_op.rejection_reason,
        source_ir_step_index=step_index,
        source_ir_output=join_op.output,
    )


def derive_relationships_from_ir(ir: PipelineIR) -> list[Relationship]:
    """
    Derive all relationships from the Join operators in a PipelineIR.

n    This is the core of FR-ONT-02: relationships are derived from join keys
    already used in the pipeline, not invented separately. We walk the IR's
    steps, find every JoinOperator, and build a Relationship from each one.

n    Only approved joins produce active relationships. Rejected joins are
    still returned (for auditability) but carry review_status="rejected_by_hitl".
    """
    relationships = []
    for i, step in enumerate(ir.steps):
        if isinstance(step, JoinOperator):
            rel = _derive_relationship_from_join(step, i, ir)
            if rel is not None:
                relationships.append(rel)
    return relationships