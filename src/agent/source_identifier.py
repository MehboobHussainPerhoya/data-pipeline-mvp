"""
Stage 1: Source Identification.

Given a natural-language request (e.g., "clean and join support tickets with
knowledge base articles"), this stage identifies which catalogued datasets
are relevant and returns a ranked list of source identification proposals.

This is the simplest stage — source identification is Low risk per the FSD,
so proposals always auto-proceed. But they still produce structured Proposal
objects with confidence and rationale, for auditability.

FSD requirements:
- FR-AGENT-01 (source identification)
- FR-AGENT-05 (confidence scoring)
- FR-AGENT-06 (explanation output)
"""

from .proposal import Proposal, ProposalType
from .confidence import score_source_identification, route_proposal
from typing import Optional


class SourceCatalogEntry:
    """One entry in the source catalog — metadata about a known dataset."""

    def __init__(
        self,
        name: str,
        source_type: str,
        location: str,
        schema_name: str,
        description: str = "",
        keywords: list[str] = None,
    ):
        self.name = name
        self.source_type = source_type
        self.location = location
        self.schema_name = schema_name
        self.description = description
        self.keywords = keywords or []

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_type": self.source_type,
            "location": self.location,
            "schema_name": self.schema_name,
            "description": self.description,
            "keywords": self.keywords,
        }


class SourceIdentifier:
    """
    Stage 1 of the agent orchestrator.

    Usage:
        identifier = SourceIdentifier(catalog=[...])
        proposals = identifier.identify_sources("clean and join tickets with KB")
        # proposals is a list of Proposal objects, ranked by confidence
    """

    def __init__(self, catalog: list[SourceCatalogEntry]):
        self._catalog = catalog

    def identify_sources(self, request: str) -> list[Proposal]:
        """
        Parse the request and return ranked source identification proposals.

        Matching logic:
        1. Tokenize the request into keywords
        2. For each catalog entry, compute keyword match (fraction of entry's
           keywords found in the request) and schema relevance (heuristic:
           does the request mention the schema name or source type?)
        3. Score each match, create a Proposal, route it by threshold
        """
        request_lower = request.lower()
        request_tokens = set(request_lower.split())

        proposals = []
        for entry in self._catalog:
            # Keyword match: fraction of entry's keywords found in request
            entry_keywords_lower = [k.lower() for k in entry.keywords]
            matched_keywords = [k for k in entry_keywords_lower if k in request_lower]
            keyword_match = len(matched_keywords) / len(entry_keywords_lower) if entry_keywords_lower else 0.0

            # Schema relevance: does the request mention the schema name or source name?
            schema_relevance = 0.0
            if entry.name.lower() in request_lower:
                schema_relevance += 0.5
            if entry.schema_name.lower() in request_lower:
                schema_relevance += 0.3
            if entry.source_type.lower() in request_lower:
                schema_relevance += 0.2
            schema_relevance = min(schema_relevance, 1.0)

            # Only propose sources with some relevance
            if keyword_match == 0.0 and schema_relevance == 0.0:
                continue

            confidence, evidence = score_source_identification(
                keyword_match=keyword_match,
                schema_relevance=schema_relevance,
            )

            matched_str = ", ".join(matched_keywords) if matched_keywords else "name/schema match"
            rationale = (
                f"Source '{entry.name}' matches request '{request}' — "
                f"matched keywords: [{matched_str}]. "
                f"Schema: {entry.schema_name}, type: {entry.source_type}."
            )

            proposal = Proposal(
                proposal_type=ProposalType.SOURCE_IDENTIFICATION,
                confidence=confidence,
                rationale=rationale,
                evidence={
                    **evidence,
                    "source_name": entry.name,
                    "source_type": entry.source_type,
                    "schema_name": entry.schema_name,
                    "matched_keywords": matched_keywords,
                },
                ir_operator=None,  # source identification doesn't produce an IR operator
            )
            proposal = route_proposal(proposal)
            proposals.append(proposal)

        # Rank by confidence (highest first)
        proposals.sort(key=lambda p: p.confidence, reverse=True)
        return proposals


# ---------------------------------------------------------------------------
# Default catalog — the 3 sources in our MVP pipeline
# ---------------------------------------------------------------------------
def get_default_catalog() -> list[SourceCatalogEntry]:
    """Return the default source catalog for the MVP support pipeline."""
    return [
        SourceCatalogEntry(
            name="support_tickets",
            source_type="csv",
            location="data/raw/support_tickets/customer_support_tickets.csv",
            schema_name="SupportCase",
            description="Customer support tickets from the ticketing system.",
            keywords=["ticket", "support", "customer", "issue", "refund", "cancellation", "billing"],
        ),
        SourceCatalogEntry(
            name="kb_articles",
            source_type="csv",
            location="data/raw/kb_articles/bitext_customer_support.csv",
            schema_name="KnowledgeArticle",
            description="Knowledge base articles with question/answer pairs.",
            keywords=["kb", "knowledge", "article", "faq", "answer", "question"],
        ),
        SourceCatalogEntry(
            name="support_activity_api",
            source_type="api",
            location="https://jsonplaceholder.typicode.com/todos",
            schema_name="SupportCase",
            description="Support activity data from the API (todo items as activity proxy).",
            keywords=["api", "activity", "support", "todo", "task"],
        ),
    ]