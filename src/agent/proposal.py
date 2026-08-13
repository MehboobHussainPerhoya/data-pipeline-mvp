"""
Proposal — the structured output of every agent decision.

Every agent stage (source identification, mapping inference, join inference,
transform generation) produces Proposal objects — never raw dicts or bare
Python logic. A Proposal wraps an IR operator (or a source/mapping descriptor)
with:
- A numeric confidence score (0-1)
- A human-readable rationale string (why the agent made this proposal)
- A review status (unreviewed / approved / rejected_by_hitl)
- The evidence used (what signals drove the confidence score)

This is the atom of the agent's output. The orchestrator collects proposals,
routes them by confidence threshold, and only proposals with review_status
"approved" (or auto-eligible) make it into the final PipelineIR.

FSD requirements:
- FR-AGENT-05 (confidence scoring)
- FR-AGENT-06 (explanation output)
- FR-PREV-02 (confidence-threshold gating)
"""

from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum


class ReviewStatus(str, Enum):
    """Lifecycle status of a proposal through the HITL gate."""
    UNREVIEWED = "unreviewed"           # just produced by the agent, not yet routed
    AUTO_APPROVED = "auto_approved"     # confidence above threshold, auto-applied
    PENDING_REVIEW = "pending_review"   # below threshold, waiting for human
    APPROVED = "approved"               # human explicitly approved
    REJECTED_BY_HITL = "rejected_by_hitl"  # human explicitly rejected
    SUPERSEDED = "superseded"           # replaced by a newer proposal


class ProposalType(str, Enum):
    """What kind of decision this proposal represents."""
    SOURCE_IDENTIFICATION = "source_identification"
    MAPPING = "mapping"
    JOIN = "join"
    TRANSFORM = "transform"


class Proposal(BaseModel):
    """
    One agent proposal — a structured, inspectable, serializable decision.

    Attributes:
        proposal_type: which stage produced this (source/mapping/join/transform)
        confidence: numeric score 0-1 (FR-AGENT-05)
        rationale: human-readable explanation (FR-AGENT-06)
        evidence: dict of signals that drove the confidence score
        review_status: where this proposal is in the HITL lifecycle
        rejection_reason: if rejected, why (FR-PREV-04 audit trail)
        ir_operator: the IR operator this proposal would add to the pipeline
                     (None for source identification proposals, which don't
                     produce IR operators directly)
        auto_eligible: whether this proposal's confidence is above the
                       threshold for auto-application (set by the orchestrator
                       based on the FSD-fixed thresholds)

    Example:
        proposal = Proposal(
            proposal_type=ProposalType.JOIN,
            confidence=0.93,
            rationale="Field 'product_id' exists in both datasets with matching types and 95% value overlap.",
            evidence={"name_match": True, "type_match": True, "value_overlap": 0.95, "uniqueness_ratio": 0.34},
            ir_operator=JoinOperator(...),
            review_status=ReviewStatus.UNREVIEWED,
        )
    """
    proposal_type: ProposalType
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence: dict = Field(default_factory=dict)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    rejection_reason: Optional[str] = None
    ir_operator: Optional[Any] = None  # IR operator (Pydantic model) or None
    auto_eligible: bool = False

    def summary(self) -> dict:
        """Return a dict summary suitable for MCP tool output / inspection."""
        return {
            "proposal_type": self.proposal_type.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "review_status": self.review_status.value,
            "rejection_reason": self.rejection_reason,
            "auto_eligible": self.auto_eligible,
            "ir_operator": self.ir_operator.model_dump() if self.ir_operator else None,
        }