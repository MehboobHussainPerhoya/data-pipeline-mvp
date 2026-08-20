"""
Phase 11B tests — Column-Level Security (FR-SEC-02).

Tests that sensitive fields are filtered out for roles without visibility,
and visible for roles with visibility. Tied to the Schema Registry (Phase 1).
"""

import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from security.rbac import Role
from security.column_security import ColumnSecurityManager, DEFAULT_SENSITIVE_VISIBLE_ROLES
from schema.registry_setup import SCHEMA_REGISTRY, get_schema


# ---------------------------------------------------------------------------
# Test Group 1: Sensitive field marking in the Schema Registry
# ---------------------------------------------------------------------------

def test_field_definition_has_sensitive_flag():
    """FieldDefinition now has a sensitive flag (default False)."""
    from schema.field_definition import FieldDefinition
    fd = FieldDefinition("test_field", "String")
    assert fd.sensitive is False

    fd_sensitive = FieldDefinition("ssn", "String", sensitive=True)
    assert fd_sensitive.sensitive is True


def test_to_dict_includes_sensitive():
    """FieldDefinition.to_dict includes the sensitive flag."""
    from schema.field_definition import FieldDefinition
    fd = FieldDefinition("ssn", "String", sensitive=True)
    d = fd.to_dict()
    assert d["sensitive"] is True


def test_mark_field_sensitive():
    """mark_field_sensitive sets the flag in the registry."""
    csm = ColumnSecurityManager()
    # Ensure clean state
    csm.unmark_field_sensitive("SupportCase", "case_id")

    csm.mark_field_sensitive("SupportCase", "case_id")
    schema = get_schema("SupportCase")
    assert schema.get_field("case_id").sensitive is True

    # Cleanup
    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_unmark_field_sensitive():
    """unmark_field_sensitive clears the flag."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")
    csm.unmark_field_sensitive("SupportCase", "case_id")
    schema = get_schema("SupportCase")
    assert schema.get_field("case_id").sensitive is False


def test_mark_nonexistent_field_raises():
    """Marking a nonexistent field raises KeyError."""
    csm = ColumnSecurityManager()
    with pytest.raises(KeyError):
        csm.mark_field_sensitive("SupportCase", "nonexistent_field")


def test_mark_nonexistent_schema_raises():
    """Marking a field in a nonexistent schema raises ValueError."""
    csm = ColumnSecurityManager()
    with pytest.raises(ValueError):
        csm.mark_field_sensitive("NonexistentSchema", "some_field")


def test_get_sensitive_fields():
    """get_sensitive_fields returns the names of sensitive fields."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")
    csm.mark_field_sensitive("SupportCase", "subject")

    sensitive = csm.get_sensitive_fields("SupportCase")
    assert "case_id" in sensitive
    assert "subject" in sensitive

    # Cleanup
    csm.unmark_field_sensitive("SupportCase", "case_id")
    csm.unmark_field_sensitive("SupportCase", "subject")


# ---------------------------------------------------------------------------
# Test Group 2: Role-based filtering
# ---------------------------------------------------------------------------

def test_viewer_cannot_see_sensitive_fields():
    """A viewer role does not see sensitive column values (nullified)."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    records = [
        {"case_id": "ticket_1", "subject": "test", "source_system": "tickets"},
        {"case_id": "ticket_2", "subject": "test2", "source_system": "tickets"},
    ]

    filtered = csm.filter_records(records, "SupportCase", Role.VIEWER)

    assert filtered[0]["case_id"] is None
    assert filtered[1]["case_id"] is None
    # Non-sensitive fields are preserved
    assert filtered[0]["subject"] == "test"
    assert filtered[1]["source_system"] == "tickets"

    # Original records are not mutated
    assert records[0]["case_id"] == "ticket_1"

    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_admin_can_see_sensitive_fields():
    """An admin role sees sensitive column values unchanged."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    records = [
        {"case_id": "ticket_1", "subject": "test", "source_system": "tickets"},
    ]

    filtered = csm.filter_records(records, "SupportCase", Role.ADMIN)

    assert filtered[0]["case_id"] == "ticket_1"
    assert filtered[0]["subject"] == "test"

    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_approver_can_see_sensitive_fields():
    """An approver role sees sensitive column values (in default config)."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    records = [{"case_id": "ticket_1", "subject": "test"}]
    filtered = csm.filter_records(records, "SupportCase", Role.APPROVER)
    assert filtered[0]["case_id"] == "ticket_1"

    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_deployer_cannot_see_sensitive_fields():
    """A deployer role does NOT see sensitive columns by default."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    records = [{"case_id": "ticket_1", "subject": "test"}]
    filtered = csm.filter_records(records, "SupportCase", Role.DEPLOYER)
    assert filtered[0]["case_id"] is None

    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_editor_cannot_see_sensitive_fields():
    """An editor role does NOT see sensitive columns by default."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    records = [{"case_id": "ticket_1", "subject": "test"}]
    filtered = csm.filter_records(records, "SupportCase", Role.EDITOR)
    assert filtered[0]["case_id"] is None

    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_filter_mode_remove():
    """mode='remove' deletes the key entirely instead of nullifying."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    records = [{"case_id": "ticket_1", "subject": "test"}]
    filtered = csm.filter_records(records, "SupportCase", Role.VIEWER, mode="remove")

    assert "case_id" not in filtered[0]
    assert filtered[0]["subject"] == "test"

    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_filter_mode_invalid_raises():
    """An invalid mode raises ValueError."""
    csm = ColumnSecurityManager()
    records = [{"case_id": "ticket_1"}]
    with pytest.raises(ValueError):
        csm.filter_records(records, "SupportCase", Role.VIEWER, mode="encrypt")


def test_no_sensitive_fields_returns_unchanged():
    """If a schema has no sensitive fields, records are returned unchanged."""
    csm = ColumnSecurityManager()
    # Ensure no sensitive fields
    for schema_name in SCHEMA_REGISTRY:
        schema = get_schema(schema_name)
        for f in schema.fields:
            f.sensitive = False

    records = [{"case_id": "ticket_1", "subject": "test"}]
    filtered = csm.filter_records(records, "SupportCase", Role.VIEWER)
    assert filtered[0]["case_id"] == "ticket_1"


def test_filter_single_record():
    """filter_record works on a single record."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    record = {"case_id": "ticket_1", "subject": "test"}
    filtered = csm.filter_record(record, "SupportCase", Role.VIEWER)
    assert filtered["case_id"] is None
    assert filtered["subject"] == "test"

    csm.unmark_field_sensitive("SupportCase", "case_id")


# ---------------------------------------------------------------------------
# Test Group 3: Field visibility report
# ---------------------------------------------------------------------------

def test_get_field_visibility_for_viewer():
    """get_field_visibility shows which fields are visible for a role."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    visibility = csm.get_field_visibility("SupportCase", Role.VIEWER)
    assert visibility["case_id"] is False  # sensitive, viewer can't see
    assert visibility["subject"] is True    # not sensitive, visible

    csm.unmark_field_sensitive("SupportCase", "case_id")


def test_get_field_visibility_for_admin():
    """Admin sees all fields."""
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    visibility = csm.get_field_visibility("SupportCase", Role.ADMIN)
    assert visibility["case_id"] is True
    assert visibility["subject"] is True

    csm.unmark_field_sensitive("SupportCase", "case_id")


# ---------------------------------------------------------------------------
# Test Group 4: Custom sensitive-visible roles
# ---------------------------------------------------------------------------

def test_custom_sensitive_visible_roles():
    """A custom set of roles that can see sensitive columns can be configured."""
    custom_roles = frozenset({Role.ADMIN, Role.DEPLOYER})
    csm = ColumnSecurityManager(sensitive_visible_roles=custom_roles)

    assert csm.can_see_sensitive(Role.ADMIN) is True
    assert csm.can_see_sensitive(Role.DEPLOYER) is True
    assert csm.can_see_sensitive(Role.APPROVER) is False  # not in custom set


def test_default_sensitive_visible_roles():
    """Default roles that can see sensitive columns are admin and approver."""
    assert Role.ADMIN in DEFAULT_SENSITIVE_VISIBLE_ROLES
    assert Role.APPROVER in DEFAULT_SENSITIVE_VISIBLE_ROLES
    assert Role.VIEWER not in DEFAULT_SENSITIVE_VISIBLE_ROLES
    assert Role.EDITOR not in DEFAULT_SENSITIVE_VISIBLE_ROLES
    assert Role.DEPLOYER not in DEFAULT_SENSITIVE_VISIBLE_ROLES


# ---------------------------------------------------------------------------
# Test Group 5: Summary
# ---------------------------------------------------------------------------

def test_summary():
    csm = ColumnSecurityManager()
    csm.mark_field_sensitive("SupportCase", "case_id")

    s = csm.summary()
    assert "admin" in s["sensitive_visible_roles"]
    assert "SupportCase" in s["schemas_with_sensitive_fields"]
    assert "case_id" in s["schemas_with_sensitive_fields"]["SupportCase"]

    csm.unmark_field_sensitive("SupportCase", "case_id")
