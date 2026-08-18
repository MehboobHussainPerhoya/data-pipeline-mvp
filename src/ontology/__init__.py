"""
Ontology package — Common Model / Ontology Layer (FSD 4.12).

Maps validated pipeline output to semantic business objects and relationships.
This layer is strictly READ-ONLY over pipeline output — it never mutates
PipelineIR, triggers re-execution, or writes to deployed output (FR-ONT-03).

Key exports:
- BusinessObjectType: a defined business object type (FR-ONT-01)
- Relationship: a link between object types derived from a real join key (FR-ONT-02)
- OntologyMapper: maps output records to object type instances (FR-ONT-01/02/03)
"""

from .object_types import BusinessObjectType, OBJECT_TYPE_REGISTRY, get_object_type
from .relationships import Relationship, RelationshipKey, derive_relationships_from_ir
from .mapper import OntologyMapper, OntologyMappingResult

__all__ = [
    "BusinessObjectType",
    "OBJECT_TYPE_REGISTRY",
    "get_object_type",
    "Relationship",
    "RelationshipKey",
    "derive_relationships_from_ir",
    "OntologyMapper",
    "OntologyMappingResult",
]