"""
Canonical type definitions for the data integration pipeline.

Each canonical type defines:
- A name (e.g., "Timestamp", "PhoneNumber")
- The underlying Python type (e.g., datetime, str)
- The set of operations valid for that type (e.g., date_diff, parse)
- Optional validation rules (functions that return True/False for a value)

These types form the vocabulary that all sources normalize into.
The TypeRegistry (type_registry.py) holds and versions these definitions.

FSD requirements: FR-REG-01 (canonical type catalog)
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from datetime import datetime


@dataclass
class CanonicalType:
    """
    Definition of one canonical type in the type registry.

    Example:
        Timestamp = CanonicalType(
            name="Timestamp",
            python_type=datetime,
            description="Point in time with date and time components",
            valid_operations=["date_diff", "parse", "format", "extract_year"],
        )
    """
    name: str
    python_type: type
    description: str
    valid_operations: list[str] = field(default_factory=list)
    validation_rules: list[Callable[[Any], bool]] = field(default_factory=list)

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        """
        Run all validation rules against a value.
        Returns (True, None) if valid, (False, reason) if not.
        """
        if value is None:
            # None is always valid — nullability is handled by the schema (Optional),
            # not by the type itself. A type's job is to validate non-null values.
            return True, None
        for rule in self.validation_rules:
            if not rule(value):
                return False, f"Validation rule failed for type '{self.name}' on value: {value!r}"
        return True, None


@dataclass
class EnumType(CanonicalType):
    """
    A canonical type with a fixed set of allowed values.

    Example:
        TicketStatus = EnumType(
            name="TicketStatus",
            python_type=str,
            description="Status of a support ticket",
            allowed_values=["Open", "Closed", "Pending"],
        )
    """
    allowed_values: list[str] = field(default_factory=list)

    def __post_init__(self):
        # Add an automatic validation rule: value must be in allowed_values
        def _is_allowed(v):
            return v in self.allowed_values
        self.validation_rules.append(_is_allowed)


@dataclass
class ArrayType(CanonicalType):
    """
    A canonical type representing a list of elements of another canonical type.

    Example:
        Tags = ArrayType(
            name="Tags",
            python_type=list,
            description="List of string tags",
            element_type="String",
        )
    """
    element_type: str = "String"  # name of the element's canonical type


# ---------------------------------------------------------------------------
# Built-in canonical type definitions
# These are the standard types available in every TypeRegistry instance.
# ---------------------------------------------------------------------------

STRING = CanonicalType(
    name="String",
    python_type=str,
    description="Standard text string",
    valid_operations=["trim", "concat", "substring", "replace", "upper", "lower", "split"],
)

TIMESTAMP = CanonicalType(
    name="Timestamp",
    python_type=datetime,
    description="Point in time with date and time components",
    valid_operations=["date_diff", "parse", "format", "extract_year", "extract_month", "extract_day"],
)

INTEGER = CanonicalType(
    name="Integer",
    python_type=int,
    description="Whole number",
    valid_operations=["add", "subtract", "multiply", "divide", "modulo"],
)

DOUBLE = CanonicalType(
    name="Double",
    python_type=float,
    description="Floating-point number",
    valid_operations=["add", "subtract", "multiply", "divide", "round", "floor", "ceil"],
)

BOOLEAN = CanonicalType(
    name="Boolean",
    python_type=bool,
    description="True or false value",
    valid_operations=["not", "and", "or"],
)

PHONE_NUMBER = CanonicalType(
    name="PhoneNumber",
    python_type=str,
    description="Phone number in E.164 or local format",
    valid_operations=["area_code_extract", "format", "validate"],
    validation_rules=[
        lambda v: isinstance(v, str) and len(v.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")) >= 7,
    ],
)

GEOMETRY_COL = CanonicalType(
    name="GeometryCol",
    python_type=str,
    description="Geographic coordinate or shape (WKT/GeoJSON representation)",
    valid_operations=["area", "distance", "contains", "intersects", "centroid"],
)

# Registry of all built-in types — used to initialize a TypeRegistry instance
BUILTIN_TYPES: list[CanonicalType] = [
    STRING,
    TIMESTAMP,
    INTEGER,
    DOUBLE,
    BOOLEAN,
    PHONE_NUMBER,
    GEOMETRY_COL,
]