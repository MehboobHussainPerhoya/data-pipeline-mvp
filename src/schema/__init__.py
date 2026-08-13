"""
Schema package — canonical types, type registry, and schema definitions.

Key exports:
- registry: the global TypeRegistry instance (all canonical types)
- get_schema: retrieve a SchemaDefinition by name
- SCHEMA_REGISTRY: dict of all registered schema definitions
"""

from .type_registry import TypeRegistry
from .field_definition import FieldDefinition
from .schema_definition import SchemaDefinition
from .registry_setup import registry, SCHEMA_REGISTRY, get_schema

__all__ = [
    "TypeRegistry",
    "FieldDefinition",
    "SchemaDefinition",
    "registry",
    "SCHEMA_REGISTRY",
    "get_schema",
]