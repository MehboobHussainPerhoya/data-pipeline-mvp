# NOTE: As of Phase 1 (Schema Registry), the canonical types and field
# definitions for these models are also defined in registry_setup.py as
# SchemaDefinitions backed by the TypeRegistry. The Pydantic models below
# remain for runtime validation; the registry is the source of truth for
# schema structure (used by IR, Type Checker, and Agent Orchestrator).
# See: src/schema/registry_setup.py

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SupportCase(BaseModel):
    case_id: str
    subject: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    channel: Optional[str] = None
    created_at: Optional[datetime] = None
    resolution: Optional[str] = None
    ticket_type: Optional[str] = None   # NEW — needed for join to KnowledgeArticle
    source_system: str


class KnowledgeArticle(BaseModel):
    """Canonical shape for a documented question/answer pair."""
    article_id: str
    category: Optional[str] = None
    intent: Optional[str] = None
    question: str   # required — an article with no question isn't valid data
    answer: str      # required — an article with no answer isn't valid data
    source_system: str  # e.g. "kb_articles"