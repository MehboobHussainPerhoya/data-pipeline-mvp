"""
Business Object Types — FR-ONT-01.

A BusinessObjectType is a semantic business object (e.g., SupportCase,
KnowledgeArticle, Customer, Product, Transaction) that maps to a dataset's
rows. It references an existing SchemaDefinition from the Schema Registry
(Phase 1) by name — it does NOT redefine fields. The schema registry remains
the single source of truth for field definitions, types, and constraints.

This module defines both:
1. The object types for our live support-case pipeline (SupportCase,
   KnowledgeArticle) — backed by schemas already in SCHEMA_REGISTRY.
2. The object types for the companion CLV pipeline (Customer, Product,
   Transaction) — these are the business objects from FSD Section 7.
   Their schemas are defined here as SchemaDefinitions because they are
   not part of the live support-case pipeline's registry, but they are
   real business objects with real fields, not synthetic placeholders.

FSD requirements: FR-ONT-01 (object type mapping)
"""

from dataclasses import dataclass, field
from typing import Optional
from schema.schema_definition import SchemaDefinition
from schema.field_definition import FieldDefinition
from schema.registry_setup import SCHEMA_REGISTRY, get_schema


@dataclass
class BusinessObjectType:
    """
    A defined business object type that a dataset's rows can be mapped to.

    Attributes:
        name: the business object type name (e.g., "SupportCase", "Transaction")
        schema: the SchemaDefinition backing this object type — fields, types,
                constraints come from here, not redefined
        identity_field: the field that uniquely identifies an instance of
                        this object type (e.g., "case_id", "product_variation_id")
        description: human-readable description of this business object

    The schema is a reference to an existing SchemaDefinition, not a copy.
    This ensures the ontology layer never diverges from the registry.
    """
    name: str
    schema: SchemaDefinition
    identity_field: str
    description: str = ""

    @property
    def field_names(self) -> list[str]:
        """Delegate to the schema — no field redefinition."""
        return self.schema.field_names

    @property
    def required_output_fields(self) -> list[str]:
        """Delegate to the schema."""
        return self.schema.required_output_fields

    def to_dict(self) -> dict:
        """Serialize for MCP tool output / inspection."""
        return {
            "name": self.name,
            "identity_field": self.identity_field,
            "description": self.description,
            "schema": self.schema.to_dict(),
        }


# ---------------------------------------------------------------------------
# Support-case pipeline object types (backed by existing SCHEMA_REGISTRY)
# These use the schemas already registered in Phase 1 — no field redefinition.
# ---------------------------------------------------------------------------
_support_case_schema = get_schema("SupportCase")
_knowledge_article_schema = get_schema("KnowledgeArticle")

support_case_type = BusinessObjectType(
    name="SupportCase",
    schema=_support_case_schema,
    identity_field="case_id",
    description="A support case/ticket from any source system.",
)

knowledge_article_type = BusinessObjectType(
    name="KnowledgeArticle",
    schema=_knowledge_article_schema,
    identity_field="article_id",
    description="A documented question/answer pair from the knowledge base.",
)

# ---------------------------------------------------------------------------
# CLV pipeline object types (FSD Section 7 companion pipeline)
# These schemas are defined here because they are not part of the live
# support-case pipeline's registry. They use real fields from the FSD's
# worked example (products/customers/transactions), not synthetic placeholders.
# ---------------------------------------------------------------------------
customer_schema = SchemaDefinition(
    name="Customer",
    description="A customer from the CLV companion pipeline (FSD Section 7).",
    fields=[
        FieldDefinition("customer_id", "String", nullable=False, required_in_output=True,
                        description="Unique customer identifier"),
        FieldDefinition("name", "String", nullable=True, required_in_output=False,
                        description="Customer name"),
        FieldDefinition("address", "String", nullable=True, required_in_output=False,
                        description="Customer address (flattened from struct)"),
    ],
)

product_schema = SchemaDefinition(
    name="Product",
    description="A product from the CLV companion pipeline (FSD Section 7).",
    fields=[
        FieldDefinition("product_variation_id", "String", nullable=False, required_in_output=True,
                        description="Unique product variation identifier — the true primary key"),
        FieldDefinition("product_id", "String", nullable=True, required_in_output=False,
                        description="Product family identifier — NOT unique (multiple variations per product)"),
        FieldDefinition("product_name", "String", nullable=True, required_in_output=False,
                        description="Product name"),
        FieldDefinition("price", "Double", nullable=True, required_in_output=False,
                        description="Product price"),
    ],
)

transaction_schema = SchemaDefinition(
    name="Transaction",
    description="A transaction from the CLV companion pipeline (FSD Section 7).",
    fields=[
        FieldDefinition("transaction_id", "String", nullable=False, required_in_output=True,
                        description="Unique transaction identifier"),
        FieldDefinition("customer_id", "String", nullable=False, required_in_output=True,
                        description="Foreign key to Customer"),
        FieldDefinition("product_id", "String", nullable=False, required_in_output=True,
                        description="Foreign key to Product — joins to product_variation_id"),
        FieldDefinition("units", "Double", nullable=True, required_in_output=False,
                        description="Quantity purchased"),
        FieldDefinition("transaction_date", "Timestamp", nullable=True, required_in_output=False,
                        description="When the transaction occurred"),
    ],
)

customer_type = BusinessObjectType(
    name="Customer",
    schema=customer_schema,
    identity_field="customer_id",
    description="A customer in the CLV companion pipeline.",
)

product_type = BusinessObjectType(
    name="Product",
    schema=product_schema,
    identity_field="product_variation_id",
    description="A product in the CLV companion pipeline. Identity field is product_variation_id, NOT product_id.",
)

transaction_type = BusinessObjectType(
    name="Transaction",
    schema=transaction_schema,
    identity_field="transaction_id",
    description="A transaction in the CLV companion pipeline.",
)

# ---------------------------------------------------------------------------
# Registry of all business object types
# ---------------------------------------------------------------------------
OBJECT_TYPE_REGISTRY: dict[str, BusinessObjectType] = {
    "SupportCase": support_case_type,
    "KnowledgeArticle": knowledge_article_type,
    "Customer": customer_type,
    "Product": product_type,
    "Transaction": transaction_type,
}


def get_object_type(name: str) -> BusinessObjectType:
    """Retrieve a BusinessObjectType by name."""
    if name not in OBJECT_TYPE_REGISTRY:
        raise ValueError(
            f"Unknown object type '{name}'. Available: {list(OBJECT_TYPE_REGISTRY.keys())}"
        )
    return OBJECT_TYPE_REGISTRY[name]