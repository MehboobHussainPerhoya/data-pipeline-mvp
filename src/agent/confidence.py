"""
Confidence scoring and threshold routing for agent proposals.

The FSD (Section 5.2) fixes the thresholds — these are NOT judgment calls:
- Source identification: Low risk, auto-proceed (always shown in UI)
- Mapping inference: Medium risk, auto-apply >= 0.85, else HITL
- Join inference: High risk, always HITL in Phase 4 (never auto-apply)
- Transform generation: Medium risk, auto-apply >= 0.85, else HITL

The scoring functions compute a 0-1 confidence from evidence signals.
Each signal contributes a weighted component; the final score is the
weighted average. This is deliberately transparent and auditable —
no black-box ML model, just explicit weighted evidence.

FSD requirements:
- FR-AGENT-05 (confidence scoring)
- FR-PREV-02 (confidence-threshold gating)
"""

from .proposal import Proposal, ProposalType, ReviewStatus


# ---------------------------------------------------------------------------
# FSD-fixed thresholds (Section 5.2) — do not change these values
# ---------------------------------------------------------------------------
MAPPING_AUTO_THRESHOLD = 0.85      # mapping proposals >= this auto-apply
TRANSFORM_AUTO_THRESHOLD = 0.85    # transform proposals >= this auto-apply
JOIN_HITL_THRESHOLD = 0.95         # join proposals >= this *could* auto-apply
                                   # — but in Phase 4, joins NEVER auto-apply


def route_proposal(proposal: Proposal) -> Proposal:
    """
    Apply the FSD-fixed routing policy to a proposal.
    Sets auto_eligible and review_status based on the proposal type and confidence.

    Rules (FSD Section 5.2):
    - Source identification: always auto_approved (Low risk)
    - Mapping: >= 0.85 auto_approved, else pending_review
    - Join: always pending_review (High risk — never auto-apply in Phase 4)
    - Transform: >= 0.85 auto_approved, else pending_review

    Returns the same proposal object with updated status fields.
    """
    if proposal.review_status != ReviewStatus.UNREVIEWED:
        # Already routed (e.g., human-approved or rejected) — don't override
        return proposal

    if proposal.proposal_type == ProposalType.SOURCE_IDENTIFICATION:
        # Low risk — auto-proceed, but shown in UI for confirmation
        proposal.auto_eligible = True
        proposal.review_status = ReviewStatus.AUTO_APPROVED

    elif proposal.proposal_type == ProposalType.MAPPING:
        if proposal.confidence >= MAPPING_AUTO_THRESHOLD:
            proposal.auto_eligible = True
            proposal.review_status = ReviewStatus.AUTO_APPROVED
        else:
            proposal.auto_eligible = False
            proposal.review_status = ReviewStatus.PENDING_REVIEW

    elif proposal.proposal_type == ProposalType.JOIN:
        # High risk — joins ALWAYS route to HITL in Phase 4, regardless of score.
        # The FSD says HITL required below 0.95, but practically: joins never
        # auto-apply in this phase. Phase 5 may wire up auto-apply for >= 0.95.
        proposal.auto_eligible = False
        proposal.review_status = ReviewStatus.PENDING_REVIEW

    elif proposal.proposal_type == ProposalType.TRANSFORM:
        if proposal.confidence >= TRANSFORM_AUTO_THRESHOLD:
            proposal.auto_eligible = True
            proposal.review_status = ReviewStatus.AUTO_APPROVED
        else:
            proposal.auto_eligible = False
            proposal.review_status = ReviewStatus.PENDING_REVIEW

    return proposal


# ---------------------------------------------------------------------------
# Scoring functions — compute 0-1 confidence from evidence signals
# ---------------------------------------------------------------------------

def score_mapping(
    name_similarity: float,
    type_compatible: bool,
    value_overlap: float = 0.0,
) -> tuple[float, dict]:
    """
    Score a mapping proposal.

    Inputs (each 0-1, except type_compatible which is bool):
    - name_similarity: how similar the source and canonical field names are
    - type_compatible: whether the source type can cast to the canonical type
    - value_overlap: fraction of sample values that match the canonical domain

    Returns (confidence, evidence_dict).
    Weights are explicit and auditable.
    """
    weights = {"name_similarity": 0.45, "type_compatible": 0.30, "value_overlap": 0.25}
    type_score = 1.0 if type_compatible else 0.0

    confidence = (
        weights["name_similarity"] * name_similarity
        + weights["type_compatible"] * type_score
        + weights["value_overlap"] * value_overlap
    )

    evidence = {
        "name_similarity": round(name_similarity, 3),
        "type_compatible": type_compatible,
        "value_overlap": round(value_overlap, 3),
        "weights": weights,
    }
    return round(confidence, 3), evidence


def score_join(
    name_match: bool,
    type_compatible: bool,
    value_overlap: float,
    uniqueness_ratio: float,
) -> tuple[float, dict]:
    """
    Score a join proposal.

    Inputs:
    - name_match: do the key field names match exactly?
    - type_compatible: are the key types compatible?
    - value_overlap: fraction of left keys that exist in the right dataset (0-1)
    - uniqueness_ratio: how unique the right key is (1.0 = perfectly unique,
      0.5 = each value appears ~2x). This is the critical signal from the
      CLV pipeline lesson — a non-unique key produces row-count skew.

    Returns (confidence, evidence_dict).

    The uniqueness_ratio is weighted heavily (0.35) because the FSD's worked
    example (Section 7, step 7) shows that a non-unique join key is the
    primary semantic error that type checking cannot catch.
    """
    weights = {
        "name_match": 0.15,
        "type_compatible": 0.20,
        "value_overlap": 0.30,
        "uniqueness_ratio": 0.35,
    }
    name_score = 1.0 if name_match else 0.0
    type_score = 1.0 if type_compatible else 0.0

    confidence = (
        weights["name_match"] * name_score
        + weights["type_compatible"] * type_score
        + weights["value_overlap"] * value_overlap
        + weights["uniqueness_ratio"] * uniqueness_ratio
    )

    evidence = {
        "name_match": name_match,
        "type_compatible": type_compatible,
        "value_overlap": round(value_overlap, 3),
        "uniqueness_ratio": round(uniqueness_ratio, 3),
        "weights": weights,
    }
    return round(confidence, 3), evidence


def score_transform(
    name_similarity: float,
    type_valid: bool,
    operation_valid: bool,
) -> tuple[float, dict]:
    """
    Score a transform proposal.

    Inputs:
    - name_similarity: how well the field name matches the intended target
    - type_valid: is the source type valid for this operation?
    - operation_valid: is the operation in the type's valid_operations set?

    Returns (confidence, evidence_dict).
    """
    weights = {"name_similarity": 0.30, "type_valid": 0.35, "operation_valid": 0.35}
    type_score = 1.0 if type_valid else 0.0
    op_score = 1.0 if operation_valid else 0.0

    confidence = (
        weights["name_similarity"] * name_similarity
        + weights["type_valid"] * type_score
        + weights["operation_valid"] * op_score
    )

    evidence = {
        "name_similarity": round(name_similarity, 3),
        "type_valid": type_valid,
        "operation_valid": operation_valid,
        "weights": weights,
    }
    return round(confidence, 3), evidence


def score_source_identification(
    keyword_match: float,
    schema_relevance: float,
) -> tuple[float, dict]:
    """
    Score a source identification proposal.

    Inputs:
    - keyword_match: fraction of request keywords found in source metadata (0-1)
    - schema_relevance: how relevant the source's schema is to the request (0-1)

    Returns (confidence, evidence_dict).
    """
    weights = {"keyword_match": 0.60, "schema_relevance": 0.40}

    confidence = (
        weights["keyword_match"] * keyword_match
        + weights["schema_relevance"] * schema_relevance
    )

    evidence = {
        "keyword_match": round(keyword_match, 3),
        "schema_relevance": round(schema_relevance, 3),
        "weights": weights,
    }
    return round(confidence, 3), evidence