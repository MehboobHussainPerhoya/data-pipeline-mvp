"""
Registry setup — creates the global TypeRegistry instance and registers
all canonical schemas (SupportCase, KnowledgeArticle, JoinedCaseOutput)
as SchemaDefinitions built from FieldDefinitions.

This module is the single place where the pipeline's canonical vocabulary
is defined. Everything else imports from here.

FSD requirements: FR-REG-01, FR-REG-03, FR-VAL-01
"""

from .type_registry import TypeRegistry
from .field_definition import FieldDefinition
from .schema_definition import SchemaDefinition

# ---------------------------------------------------------------------------
# Create the global registry instance (starts with built-in types at v1)
# ---------------------------------------------------------------------------
registry = TypeRegistry()

# ---------------------------------------------------------------------------
# SupportCase schema
# Mirrors the existing Pydantic SupportCase, but now every field references
# a canonical type from the registry.
# ---------------------------------------------------------------------------
support_case_schema = SchemaDefinition(
    name="SupportCase",
    description="A support case/ticket from any source system, normalized to canonical form.",
    fields=[
        FieldDefinition("case_id", "String", nullable=False, required_in_output=True,
                        description="Unique identifier with source prefix (e.g., ticket_1, api_5)"),
        FieldDefinition("subject", "String", nullable=True, required_in_output=True,
                        description="Subject/title of the support case"),
        FieldDefinition("description", "String", nullable=True, required_in_output=False,
                        description="Full description of the issue"),
        FieldDefinition("status", "String", nullable=True, required_in_output=False,
                        description="Current status (Open, Closed, Pending, etc.)"),
        FieldDefinition("priority", "String", nullable=True, required_in_output=False,
                        description="Priority level (Low, Medium, High, etc.)"),
        FieldDefinition("channel", "String", nullable=True, required_in_output=False,
                        description="Channel of contact (Email, Chat, Phone, etc.)"),
        FieldDefinition("created_at", "Timestamp", nullable=True, required_in_output=False,
                        description="When the case was created"),
        FieldDefinition("resolution", "String", nullable=True, required_in_output=False,
                        description="Resolution details if resolved"),
        FieldDefinition("ticket_type", "String", nullable=True, required_in_output=False,
                        description="Type of ticket — used for join to KnowledgeArticle"),
        FieldDefinition("source_system", "String", nullable=False, required_in_output=True,
                        description="Origin system identifier for provenance"),
    ],
)

# ---------------------------------------------------------------------------
# KnowledgeArticle schema
# ---------------------------------------------------------------------------
knowledge_article_schema = SchemaDefinition(
    name="KnowledgeArticle",
    description="A documented question/answer pair from the knowledge base.",
    fields=[
        FieldDefinition("article_id", "String", nullable=False, required_in_output=True,
                        description="Unique identifier (e.g., kb_0)"),
        FieldDefinition("category", "String", nullable=True, required_in_output=False,
                        description="KB category (REFUND, CANCEL, PAYMENT, etc.)"),
        FieldDefinition("intent", "String", nullable=True, required_in_output=False,
                        description="Intent tag from the KB dataset"),
        FieldDefinition("question", "String", nullable=False, required_in_output=True,
                        description="The question/instruction — required, no blank articles"),
        FieldDefinition("answer", "String", nullable=False, required_in_output=True,
                        description="The answer/response — required, no blank articles"),
        FieldDefinition("source_system", "String", nullable=False, required_in_output=True,
                        description="Origin system identifier"),
    ],
)

# ---------------------------------------------------------------------------
# JoinedCaseOutput schema (the final output contract)
# ---------------------------------------------------------------------------
joined_case_output_schema = SchemaDefinition(
    name="JoinedCaseOutput",
    description="Final deployable shape of one support case after joining. Stricter than SupportCase.",
    fields=[
        FieldDefinition("case_id", "String", nullable=False, required_in_output=True,
                        description="Unique case identifier"),
        FieldDefinition("subject", "String", nullable=False, required_in_output=True,
                        description="Subject — required in final output, no blanks allowed"),
        FieldDefinition("ticket_type", "String", nullable=True, required_in_output=False,
                        description="Type of ticket"),
        FieldDefinition("matched_category", "String", nullable=True, required_in_output=False,
                        description="KB category matched via join"),
        FieldDefinition("matched_article_count", "Integer", nullable=False, required_in_output=True,
                        description="Number of KB articles matched"),
        FieldDefinition("source_system", "String", nullable=False, required_in_output=True,
                        description="Origin system identifier"),
    ],
)

# ---------------------------------------------------------------------------
# Registry of all schema definitions (name -> SchemaDefinition)
# ---------------------------------------------------------------------------
SCHEMA_REGISTRY: dict[str, SchemaDefinition] = {
    "SupportCase": support_case_schema,
    "KnowledgeArticle": knowledge_article_schema,
    "JoinedCaseOutput": joined_case_output_schema,
}


def get_schema(name: str) -> SchemaDefinition:
    """Retrieve a SchemaDefinition by name."""
    if name not in SCHEMA_REGISTRY:
        raise ValueError(f"Unknown schema '{name}'. Available: {list(SCHEMA_REGISTRY.keys())}")
    return SCHEMA_REGISTRY[name]