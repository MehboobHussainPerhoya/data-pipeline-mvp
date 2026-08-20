"""
Column-Level Security — FR-SEC-02.

Supports restricting visibility of sensitive columns (e.g. tax IDs) by role.
Ties into the Schema Registry (Phase 1) — a field can be marked sensitive
there (via FieldDefinition.sensitive=True), and this module provides a
role-aware read path that filters sensitive fields out for roles without
visibility.

This does NOT build a separate schema system. It reads the existing
SCHEMA_REGISTRY from schema.registry_setup and the RBAC roles from
security.rbac, and filters records based on which fields the actor's
role is allowed to see.

Design:
- A role either has column-level sensitive visibility or it doesn't.
  By default, only admin and approver roles can see sensitive columns.
  This is configurable via the sensitive_visible_roles parameter.
- filter_records() takes a list of record dicts, a schema name, and an
  actor's role, and returns a new list with sensitive fields removed
  (set to None or fully omitted) for roles without visibility.
- mark_field_sensitive() / unmark_field_sensitive() allow runtime marking
  of fields as sensitive in the Schema Registry.

FSD requirements: FR-SEC-02 (Column-level security, S)
"""

from schema.registry_setup import SCHEMA_REGISTRY, get_schema
from schema.schema_definition import SchemaDefinition
from schema.field_definition import FieldDefinition
from security.rbac import Role


# ---------------------------------------------------------------------------
# Default roles that can see sensitive columns (FSD Section 8)
# Admin and Approver (Pipeline Manager) can see sensitive data.
# Viewer, Editor, and Deployer cannot — they don't need tax IDs to do
# their jobs. This is configurable.
# ---------------------------------------------------------------------------
DEFAULT_SENSITIVE_VISIBLE_ROLES: frozenset[Role] = frozenset({
    Role.ADMIN,
    Role.APPROVER,
})


class ColumnSecurityManager:
    """
    Manages column-level security: marking fields as sensitive and filtering
    records by role so that roles without sensitive visibility don't see
    sensitive column values.

    Ties into the existing Schema Registry (Phase 1) — does not build a
    separate schema system. A field is sensitive if its FieldDefinition
    has sensitive=True in the registry.

    Usage:
        csm = ColumnSecurityManager()
        csm.mark_field_sensitive("SupportCase", "case_id")  # mark at runtime
        filtered = csm.filter_records(records, "SupportCase", Role.VIEWER)
        # sensitive fields are removed/None for viewer
    """

    def __init__(
        self,
        sensitive_visible_roles: frozenset[Role] = DEFAULT_SENSITIVE_VISIBLE_ROLES,
    ):
        self._sensitive_visible_roles = sensitive_visible_roles

    def can_see_sensitive(self, role: Role) -> bool:
        """Check if a role is allowed to see sensitive columns."""
        return role in self._sensitive_visible_roles

    def get_sensitive_fields(self, schema_name: str) -> list[str]:
        """
        Return the names of all sensitive fields in a schema.

        Reads from the Schema Registry — a field is sensitive if its
        FieldDefinition.sensitive is True.
        """
        schema = get_schema(schema_name)
        return [f.name for f in schema.fields if f.sensitive]

    def mark_field_sensitive(self, schema_name: str, field_name: str) -> None:
        """
        Mark a field as sensitive in the Schema Registry.

        Args:
            schema_name: the schema containing the field
            field_name: the field to mark sensitive

        Raises:
            KeyError: if schema or field doesn't exist
        """
        schema = get_schema(schema_name)
        field_def = schema.get_field(field_name)  # raises KeyError if not found
        field_def.sensitive = True

    def unmark_field_sensitive(self, schema_name: str, field_name: str) -> None:
        """Remove the sensitive marking from a field."""
        schema = get_schema(schema_name)
        field_def = schema.get_field(field_name)
        field_def.sensitive = False

    def filter_records(
        self,
        records: list[dict],
        schema_name: str,
        role: Role,
        mode: str = "nullify",
    ) -> list[dict]:
        """
        Filter sensitive columns from records based on the actor's role.

        If the role can see sensitive columns (can_see_sensitive), the
        records are returned unchanged. Otherwise, sensitive fields are
        either set to None (mode="nullify") or removed from the dict
        (mode="remove").

        Args:
            records: list of record dicts to filter
            schema_name: the schema name to check for sensitive fields
            role: the actor's role
            mode: "nullify" (set to None) or "remove" (delete the key)

        Returns:
            A new list of filtered record dicts (originals are not mutated).
        """
        if mode not in ("nullify", "remove"):
            raise ValueError(f"Invalid mode '{mode}'. Must be 'nullify' or 'remove'.")

        # If the role can see sensitive data, return copies unchanged
        if self.can_see_sensitive(role):
            return [dict(r) for r in records]

        sensitive_fields = self.get_sensitive_fields(schema_name)
        if not sensitive_fields:
            # No sensitive fields in this schema — return copies unchanged
            return [dict(r) for r in records]

        filtered = []
        for record in records:
            new_record = dict(record)
            for sf in sensitive_fields:
                if sf in new_record:
                    if mode == "nullify":
                        new_record[sf] = None
                    else:  # remove
                        del new_record[sf]
            filtered.append(new_record)
        return filtered

    def filter_record(
        self,
        record: dict,
        schema_name: str,
        role: Role,
        mode: str = "nullify",
    ) -> dict:
        """Filter sensitive columns from a single record. See filter_records."""
        return self.filter_records([record], schema_name, role, mode=mode)[0]

    def get_field_visibility(self, schema_name: str, role: Role) -> dict:
        """
        Return a visibility report for all fields in a schema for a given role.

        Returns a dict mapping field_name -> visible (bool).
        """
        schema = get_schema(schema_name)
        can_see = self.can_see_sensitive(role)
        return {
            f.name: (can_see if f.sensitive else True)
            for f in schema.fields
        }

    def summary(self) -> dict:
        """Return a summary of column security state for MCP tool output."""
        return {
            "sensitive_visible_roles": [r.value for r in self._sensitive_visible_roles],
            "schemas_with_sensitive_fields": {
                name: [f.name for f in schema.fields if f.sensitive]
                for name, schema in SCHEMA_REGISTRY.items()
                if any(f.sensitive for f in schema.fields)
            },
        }
