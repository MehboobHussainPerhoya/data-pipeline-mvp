"""
FieldDefinition — the bridge between the TypeRegistry and canonical schemas.

A FieldDefinition is one named field in a schema (e.g., "created_at") that:
- References a canonical type from the registry (e.g., "Timestamp")
- Declares whether it's nullable (Optional) or required
- Declares whether it's required in the final output contract
- Declares whether it's sensitive (Phase 11, FR-SEC-02 — column-level security)

This lets us build schemas that are registry-aware, rather than hardcoding
Python types directly in Pydantic models.

FSD requirements: FR-REG-01 (canonical type catalog), FR-VAL-01 (required-column tracking),
                   FR-SEC-02 (column-level security)
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from .type_registry import TypeRegistry


@dataclass
class FieldDefinition:
    """
    Definition of one field in a canonical schema.

    Example:
        created_at = FieldDefinition(
            name="created_at",
            canonical_type="Timestamp",
            nullable=True,          # can be None mid-pipeline
            required_in_output=False,  # not required in final output
        )
    """
    name: str
    canonical_type: str  # name of a type in the TypeRegistry
    nullable: bool = True
    required_in_output: bool = False
    description: str = ""
    sensitive: bool = False  # FR-SEC-02: mark sensitive columns (e.g. tax IDs) for role-based filtering

    def validate(self, value: Any, registry: TypeRegistry) -> tuple[bool, Optional[str]]:
        """
        Validate a value against this field definition using the registry.
        Checks: type-level validation rules + nullability.
        """
        # Nullability check
        if value is None:
            if not self.nullable:
                return False, f"Field '{self.name}' is not nullable but got None."
            return True, None  # None is valid for nullable fields

        # Type-level validation
        canonical = registry.get_type(self.canonical_type)
        return canonical.validate(value)

    def to_dict(self) -> dict:
        """Serialize for MCP tool output / inspection."""
        return {
            "name": self.name,
            "canonical_type": self.canonical_type,
            "nullable": self.nullable,
            "required_in_output": self.required_in_output,
            "description": self.description,
            "sensitive": self.sensitive,
        }