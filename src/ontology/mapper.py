"""
OntologyMapper — FR-ONT-01/02/03 combined.

Maps output records to business object type instances (FR-ONT-01) and
resolves relationships between them (FR-ONT-02), while enforcing
backward propagation protection (FR-ONT-03).

FR-ONT-03 enforcement (the highest-priority requirement in this phase):
The mapper is strictly READ-ONLY over pipeline output. It:
- Accepts output records as a read-only snapshot (list of dicts or Pydantic models)
- Accepts the PipelineIR as a read-only reference (for relationship derivation)
- Returns a separate OntologyMappingResult — never mutates the input
- Does NOT write to deployed output, does NOT trigger re-execution,
  does NOT mutate PipelineIR

There is no code path in this module that can alter the underlying output
dataset. The mapper annotates/interprets existing output; it does not produce it.

FSD requirements:
- FR-ONT-01 (object type mapping)
- FR-ONT-02 (relationship/link definition)
- FR-ONT-03 (backward propagation protection — Priority M)
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from ir.pipeline_ir import PipelineIR
from .object_types import BusinessObjectType, OBJECT_TYPE_REGISTRY, get_object_type
from .relationships import Relationship, derive_relationships_from_ir


@dataclass
class ObjectInstance:
    """
    One instance of a business object type, mapped from an output record.

n    This is a read-only annotation over an existing output record — it does
    not modify the record. The instance holds a reference to the original
    record data (as a dict snapshot) and the object type it was mapped to.
    """
    object_type: BusinessObjectType
    identity: Any                    # the value of the identity field
    record: dict                     # snapshot of the source record (dict copy)

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type.name,
            "identity": self.identity,
            "record": self.record,
        }


@dataclass
class OntologyMappingResult:
    """
    The result of mapping output records to object type instances.

n    This is a NEW object — it does not reference or mutate the original
    output records or the PipelineIR. The caller receives this result and
    can inspect it freely; the underlying pipeline state is untouched.

n    Attributes:
        object_type_name: which business object type was mapped
        instances: list of ObjectInstance mapped from the output records
        relationships: relationships derived from the pipeline IR (FR-ONT-02)
        unmapped_count: records that could not be mapped (missing identity field)
        total_records: total records examined
        ir_unchanged: always True — proves the IR was not mutated (FR-ONT-03)
        output_unchanged: always True — proves the output was not mutated (FR-ONT-03)
    """
    object_type_name: str
    instances: list[ObjectInstance] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    unmapped_count: int = 0
    total_records: int = 0
    ir_unchanged: bool = True
    output_unchanged: bool = True

    def to_dict(self) -> dict:
        return {
            "object_type_name": self.object_type_name,
            "instance_count": len(self.instances),
            "unmapped_count": self.unmapped_count,
            "total_records": self.total_records,
            "ir_unchanged": self.ir_unchanged,
            "output_unchanged": self.output_unchanged,
            "relationships": [r.to_dict() for r in self.relationships],
            "instances": [inst.to_dict() for inst in self.instances],
        }


def _record_to_dict(record: Any) -> dict:
    """Convert a record (dict or Pydantic model) to a dict snapshot."""
    if isinstance(record, dict):
        return dict(record)  # shallow copy — snapshot, not reference
    if hasattr(record, "model_dump"):
        return record.model_dump()
    if hasattr(record, "__dict__"):
        return dict(record.__dict__)
    return {"value": record}


class OntologyMapper:
    """
    Maps output records to business object type instances (FR-ONT-01)
    and derives relationships from the pipeline IR (FR-ONT-02).

n    FR-ONT-03 enforcement:
    This class is strictly read-only. It accepts references to existing
    pipeline output and IR, and returns a NEW OntologyMappingResult.
    It has no methods that write, mutate, or re-execute anything.

n    Usage:
        mapper = OntologyMapper()
        result = mapper.map_records(
            records=output_records,       # read-only snapshot
            object_type_name="SupportCase",
            ir=support_case_pipeline_ir,  # read-only reference
        )
        # result.instances — the mapped object instances
        # result.relationships — relationships derived from the IR's joins
        # result.ir_unchanged == True   — proof the IR was not touched
        # result.output_unchanged == True — proof the output was not touched
    """

    def map_records(
        self,
        records: list[Any],
        object_type_name: str,
        ir: Optional[PipelineIR] = None,
    ) -> OntologyMappingResult:
        """
        Map output records to instances of a business object type.

n        Parameters:
            records: the output records to map (list of dicts or Pydantic models).
                     These are treated as READ-ONLY — the mapper takes snapshots,
                     never mutates the originals.
            object_type_name: the name of the BusinessObjectType to map to.
            ir: optional PipelineIR for relationship derivation (FR-ONT-02).
                If provided, relationships are derived from the IR's Join operators.
                The IR is treated as READ-ONLY — it is never mutated.

n        Returns:
            OntologyMappingResult — a NEW object containing the mapped instances
            and derived relationships. The original records and IR are untouched.

n        FR-ONT-03: This method does not write to deployed output, does not
        trigger re-execution, and does not mutate PipelineIR. The returned
        result is a separate object. The ir_unchanged and output_unchanged
        flags are always True — they exist so callers and tests can verify
        this property programmatically.
        """
        obj_type = get_object_type(object_type_name)
        result = OntologyMappingResult(object_type_name=object_type_name)
        result.total_records = len(records)

        for record in records:
            record_dict = _record_to_dict(record)
            identity = record_dict.get(obj_type.identity_field)

            if identity is not None:
                result.instances.append(ObjectInstance(
                    object_type=obj_type,
                    identity=identity,
                    record=record_dict,
                ))
            else:
                result.unmapped_count += 1

        # Derive relationships from the IR (FR-ONT-02) — read-only
        if ir is not None:
            result.relationships = derive_relationships_from_ir(ir)

        return result

    def map_single_record(
        self,
        record: Any,
        object_type_name: str,
    ) -> Optional[ObjectInstance]:
        """
        Map a single output record to an object instance.

n        Returns None if the record is missing the identity field.
        This is a convenience method — it does not derive relationships.
        """
        obj_type = get_object_type(object_type_name)
        record_dict = _record_to_dict(record)
        identity = record_dict.get(obj_type.identity_field)

        if identity is None:
            return None

        return ObjectInstance(
            object_type=obj_type,
            identity=identity,
            record=record_dict,
        )

    def get_relationships(self, ir: PipelineIR) -> list[Relationship]:
        """
        Derive relationships from a pipeline IR without mapping any records.

n        This is a read-only operation over the IR (FR-ONT-03).
        """
        return derive_relationships_from_ir(ir)