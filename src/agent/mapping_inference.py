"""
Stage 2: Mapping Inference.

For each candidate source, this stage proposes field-to-canonical-field mappings
based on name similarity, type compatibility, and sample-value profiling.

A mapping proposal says: "source field X should map to canonical field Y"
with a confidence score and rationale. If confidence >= 0.85, the proposal
is auto-eligible; otherwise it routes to HITL review.

FSD requirements:
- FR-AGENT-02 (mapping inference)
- FR-AGENT-05 (confidence scoring)
- FR-AGENT-06 (explanation output)
"""

from .proposal import Proposal, ProposalType
from .confidence import score_mapping, route_proposal
from typing import Optional
import re


def _normalize_name(name: str) -> str:
    """Normalize a field name for comparison: lowercase, remove separators."""
    return re.sub(r'[\s_\-]+', '', name.lower())


def _name_similarity(a: str, b: str) -> float:
    """
    Compute name similarity between two field names (0-1).
    Uses a combination of exact match, substring match, and token overlap.
    """
    na = _normalize_name(a)
    nb = _normalize_name(b)

    if na == nb:
        return 1.0

    # Substring match (one contains the other)
    if na in nb or nb in na:
        return 0.85

    # Token overlap (Jaccard similarity on word tokens)
    tokens_a = set(re.split(r'[\s_\-]+', a.lower()))
    tokens_b = set(re.split(r'[\s_\-]+', b.lower()))
    tokens_a.discard("")
    tokens_b.discard("")
    if tokens_a and tokens_b:
        overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
        if overlap > 0:
            return overlap

    # Character-level overlap (last resort)
    from difflib import SequenceMatcher
    return SequenceMatcher(None, na, nb).ratio()


def _infer_type(raw_values: list) -> str:
    """
    Infer the canonical type from sample values.
    Returns one of: 'String', 'Integer', 'Double', 'Timestamp', 'Boolean'.
    """
    non_null = [v for v in raw_values if v is not None and v != ""]
    if not non_null:
        return "String"

    # Check Boolean
    bool_like = all(str(v).lower() in ("true", "false", "0", "1") for v in non_null[:50])
    if bool_like:
        return "Boolean"

    # Check Integer
    try:
        int(non_null[0])
        int_like = all(self._is_int(v) for v in non_null[:50])
        if int_like:
            return "Integer"
    except (ValueError, TypeError):
        pass

    # Check Double
    try:
        float(non_null[0])
        float_like = all(self._is_float(v) for v in non_null[:50])
        if float_like:
            return "Double"
    except (ValueError, TypeError):
        pass

    # Check Timestamp
    if any("-" in str(v) and ":" in str(v) for v in non_null[:50]):
        return "Timestamp"
    if any(re.match(r'\d{4}-\d{2}-\d{2}', str(v)) for v in non_null[:50]):
        return "Timestamp"

    return "String"


def _is_int(v) -> bool:
    try:
        int(v)
        return True
    except (ValueError, TypeError):
        return False


def _is_float(v) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _type_compatible(source_type: str, canonical_type: str) -> bool:
    """Check if a source type can be cast to a canonical type."""
    if source_type == canonical_type:
        return True
    # Integer -> Double is safe
    if source_type == "Integer" and canonical_type == "Double":
        return True
    # Integer/Double -> String is safe (stringification)
    if source_type in ("Integer", "Double") and canonical_type == "String":
        return True
    # Anything -> String is safe
    if canonical_type == "String":
        return True
    return False


def _value_overlap(source_values: list, canonical_domain: list) -> float:
    """
    Compute what fraction of source values appear in the canonical domain.
    Returns 0-1.
    """
    non_null = [v for v in source_values if v is not None and v != ""]
    if not non_null or not canonical_domain:
        return 0.0
    canonical_set = set(str(v) for v in canonical_domain)
    matching = sum(1 for v in non_null if str(v) in canonical_set)
    return matching / len(non_null)


class SourceFieldProfile:
    """Profile of one field in a raw source — name, inferred type, sample values."""

    def __init__(self, name: str, sample_values: list = None):
        self.name = name
        self.sample_values = sample_values or []
        self.inferred_type = _infer_type(self.sample_values)


class CanonicalFieldTarget:
    """One canonical field that a source field could map to."""

    def __init__(self, name: str, canonical_type: str, domain_values: list = None):
        self.name = name
        self.canonical_type = canonical_type
        self.domain_values = domain_values or []


class MappingInference:
    """
    Stage 2 of the agent orchestrator.

    Usage:
        inferrer = MappingInference()
        proposals = inferrer.infer_mappings(
            source_name="support_tickets",
            source_fields=[SourceFieldProfile(...), ...],
            canonical_fields=[CanonicalFieldTarget(...), ...],
        )
    """

    def infer_mappings(
        self,
        source_name: str,
        source_fields: list[SourceFieldProfile],
        canonical_fields: list[CanonicalFieldTarget],
    ) -> list[Proposal]:
        """
        Propose field-to-canonical-field mappings for one source.

        For each source field, find the best-matching canonical field and
        create a Proposal with the confidence score and rationale.
        """
        proposals = []

        for src_field in source_fields:
            best_match = None
            best_confidence = 0.0
            best_evidence = {}
            best_canonical = None

            for canon_field in canonical_fields:
                sim = _name_similarity(src_field.name, canon_field.name)
                type_ok = _type_compatible(src_field.inferred_type, canon_field.canonical_type)
                overlap = _value_overlap(src_field.sample_values, canon_field.domain_values)

                confidence, evidence = score_mapping(
                    name_similarity=sim,
                    type_compatible=type_ok,
                    value_overlap=overlap,
                )

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_evidence = evidence
                    best_match = sim
                    best_canonical = canon_field

            if best_canonical and best_confidence > 0:
                rationale = (
                    f"Map source field '{src_field.name}' ({src_field.inferred_type}) "
                    f"to canonical field '{best_canonical.name}' ({best_canonical.canonical_type}). "
                    f"Name similarity: {best_match:.2f}, type compatible: {best_evidence['type_compatible']}, "
                    f"value overlap: {best_evidence['value_overlap']:.2f}."
                )

                proposal = Proposal(
                    proposal_type=ProposalType.MAPPING,
                    confidence=best_confidence,
                    rationale=rationale,
                    evidence={
                        **best_evidence,
                        "source_field": src_field.name,
                        "source_type": src_field.inferred_type,
                        "canonical_field": best_canonical.name,
                        "canonical_type": best_canonical.canonical_type,
                        "source_name": source_name,
                    },
                    ir_operator=None,  # mapping proposals don't directly produce IR operators;
                                       # the orchestrator converts approved mappings into Map operators
                )
                proposal = route_proposal(proposal)
                proposals.append(proposal)

        return proposals