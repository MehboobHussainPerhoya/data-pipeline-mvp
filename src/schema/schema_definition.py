"""
SchemaDefinition — a named collection of FieldDefinitions.

A SchemaDefinition is the canonical shape of a dataset (e.g., "SupportCase").
It is built from FieldDefinitions, each referencing a canonical type from
the TypeRegistry. This replaces raw Pydantic models as the primary schema
definition — Pydantic models are now generated FROM the schema definition,
so the registry is always the source of truth.

FSD requirements: FR-REG-01 (canonical type catalog), FR-VAL-01 (required-column tracking)
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from .field_definition import FieldDefinition
from .type_registry import TypeRegistry


@dataclass
class SchemaDefinition:
    """
    Definition of a canonical schema (e.g., SupportCase, KnowledgeArticle).

    Example:
        support_case_schema = SchemaDefinition(
            name="SupportCase",
            fields=[
                FieldDefinition("case_id", "String", nullable=False, required_in_output=True),
                FieldDefinition("subject", "String", nullable=True, required_in_output=True),
                ...
            ],
        )
    """
    name: str
    fields: list[FieldDefinition] = field(default_factory=list)
    description: str = ""

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    @property
    def required_output_fields(self) -> list[str]:
        """Fields marked as required in the final output contract."""
        return [f.name for f in self.fields if f.required_in_output]

    def get_field(self, name: str) -> FieldDefinition:
        """Retrieve a field definition by name."""
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"Field '{name}' not found in schema '{self.name}'. Available: {self.field_names}")

    def validate_record(self, record: dict, registry: TypeRegistry) -> tuple[list[str], list[tuple[str, str]]]:
        """
        Validate a record (dict) against this schema using the registry.
        Returns (valid_fields, errors) where errors is a list of (field_name, error_message).
        """
        valid_fields = []
        errors = []

        for field_def in self.fields:
            value = record.get(field_def.name)
            is_valid, reason = field_def.validate(value, registry)
            if is_valid:
                valid_fields.append(field_def.name)
            else:
                errors.append((field_def.name, reason))

        return valid_fields, errors

    def required_column_status(self, record: dict) -> tuple[int, int, list[str]]:
        """
        Check how many required output columns are satisfied by a record.
        Returns (satisfied_count, total_required, list_of_missing).

        FSD: FR-VAL-01 (required-column tracking)
        """
        required = self.required_output_fields
        missing = [f for f in required if record.get(f) is None]
        return len(required) - len(missing), len(required), missing

    def to_dict(self) -> dict:
        """Serialize for MCP tool output / inspection."""
        return {
            "name": self.name,
            "description": self.description,
            "fields": [f.to_dict() for f in self.fields],
            "required_output_fields": self.required_output_fields,
        }