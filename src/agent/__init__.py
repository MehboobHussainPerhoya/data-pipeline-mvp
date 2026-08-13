"""
Agent package — AI Agent Orchestrator for the data integration pipeline.

Key exports:
- Proposal: structured output of every agent decision
- AgentOrchestrator: ties all 4 stages together
- SourceIdentifier, MappingInference, JoinInference, TransformGeneration: individual stages
- Confidence scoring functions and FSD-fixed thresholds

FSD requirements: FR-AGENT-01 through FR-AGENT-07
"""

from .proposal import Proposal, ProposalType, ReviewStatus
from .confidence import (
    route_proposal,
    score_mapping,
    score_join,
    score_transform,
    score_source_identification,
    MAPPING_AUTO_THRESHOLD,
    TRANSFORM_AUTO_THRESHOLD,
    JOIN_HITL_THRESHOLD,
)
from .source_identifier import SourceIdentifier, SourceCatalogEntry, get_default_catalog
from .mapping_inference import MappingInference, SourceFieldProfile, CanonicalFieldTarget
from .join_inference import JoinInference, JoinKeyCandidate
from .transform_generation import TransformGeneration
from .orchestrator import AgentOrchestrator, OrchestratorResult

__all__ = [
    "Proposal", "ProposalType", "ReviewStatus",
    "route_proposal", "score_mapping", "score_join", "score_transform", "score_source_identification",
    "MAPPING_AUTO_THRESHOLD", "TRANSFORM_AUTO_THRESHOLD", "JOIN_HITL_THRESHOLD",
    "SourceIdentifier", "SourceCatalogEntry", "get_default_catalog",
    "MappingInference", "SourceFieldProfile", "CanonicalFieldTarget",
    "JoinInference", "JoinKeyCandidate",
    "TransformGeneration",
    "AgentOrchestrator", "OrchestratorResult",
]