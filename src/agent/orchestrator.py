"""
Agent Orchestrator — ties all 4 stages together.

Given a natural-language request and source data profiles, the orchestrator:
1. Identifies candidate sources (SourceIdentifier)
2. Infers field mappings for each source (MappingInference)
3. Infers join keys between sources (JoinInference)
4. Generates transforms (TransformGeneration)
5. Routes all proposals by confidence threshold
6. Returns a structured result with all proposals, grouped by stage and review status

The orchestrator does NOT execute anything — it only produces proposals.
Execution is the IR executor's job, after proposals are approved.

FSD requirements:
- FR-AGENT-01 through FR-AGENT-07
- FR-PREV-02 (confidence-threshold gating)
"""

from .proposal import Proposal, ProposalType, ReviewStatus
from .source_identifier import SourceIdentifier, SourceCatalogEntry, get_default_catalog
from .mapping_inference import MappingInference, SourceFieldProfile, CanonicalFieldTarget
from .join_inference import JoinInference, JoinKeyCandidate
from .transform_generation import TransformGeneration
from pydantic import BaseModel
from typing import Optional, Any


class OrchestratorResult(BaseModel):
    """Structured result of a full orchestration run."""
    request: str
    source_proposals: list[dict] = []
    mapping_proposals: list[dict] = []
    join_proposals: list[dict] = []
    transform_proposals: list[dict] = []
    all_proposals: list[dict] = []
    raw_proposals: list[Any] = []      # actual Proposal objects for HITLGate
    auto_approved_count: int = 0
    pending_review_count: int = 0
    rejected_count: int = 0

    def summary(self) -> dict:
        return {
            "request": self.request,
            "total_proposals": len(self.all_proposals),
            "auto_approved": self.auto_approved_count,
            "pending_review": self.pending_review_count,
            "rejected": self.rejected_count,
            "by_type": {
                "source": len(self.source_proposals),
                "mapping": len(self.mapping_proposals),
                "join": len(self.join_proposals),
                "transform": len(self.transform_proposals),
            },
        }


class AgentOrchestrator:
    """
    The full AI agent orchestrator — all 4 stages.

    Usage:
        orchestrator = AgentOrchestrator()
        result = orchestrator.orchestrate(
            request="clean and join support tickets with knowledge base",
            source_profiles={...},
            join_candidates=[...],
        )
        # result.source_proposals, result.mapping_proposals, etc.
    """

    def __init__(self, catalog: list[SourceCatalogEntry] = None):
        self._catalog = catalog or get_default_catalog()
        self._source_identifier = SourceIdentifier(self._catalog)
        self._mapping_inferrer = MappingInference()
        self._join_inferrer = JoinInference()
        self._transform_generator = TransformGeneration()

    def orchestrate(
        self,
        request: str,
        source_profiles: dict[str, list[SourceFieldProfile]] = None,
        canonical_fields: list[CanonicalFieldTarget] = None,
        join_candidates: list[JoinKeyCandidate] = None,
        join_pairs: list[tuple[str, str]] = None,
    ) -> OrchestratorResult:
        """
        Run all 4 stages and return a structured result.

        Parameters:
        - request: the natural-language request
        - source_profiles: dict mapping source name -> list of SourceFieldProfile
        - canonical_fields: list of CanonicalFieldTarget for mapping inference
        - join_candidates: list of JoinKeyCandidate for join inference
        - join_pairs: list of (left_name, right_name) tuples specifying which
          dataset pairs to consider for joins

        Returns an OrchestratorResult with all proposals grouped by stage.
        """
        source_profiles = source_profiles or {}
        canonical_fields = canonical_fields or []
        join_candidates = join_candidates or []
        join_pairs = join_pairs or []

        all_proposals: list[Proposal] = []

        # --- Stage 1: Source Identification ---
        source_proposals = self._source_identifier.identify_sources(request)
        all_proposals.extend(source_proposals)

        # --- Stage 2: Mapping Inference ---
        mapping_proposals = []
        for source_name, fields in source_profiles.items():
            if canonical_fields:
                mappings = self._mapping_inferrer.infer_mappings(
                    source_name=source_name,
                    source_fields=fields,
                    canonical_fields=canonical_fields,
                )
                mapping_proposals.extend(mappings)
        all_proposals.extend(mapping_proposals)

        # --- Stage 3: Join Inference ---
        join_proposals = []
        for left_name, right_name in join_pairs:
            # Filter candidates for this pair
            pair_candidates = [
                c for c in join_candidates
                if c.left_key and c.right_key  # basic validity
            ]
            if pair_candidates:
                joins = self._join_inferrer.infer_joins(
                    left_name=left_name,
                    right_name=right_name,
                    candidates=pair_candidates,
                )
                join_proposals.extend(joins)
        all_proposals.extend(join_proposals)

        # --- Stage 4: Transform Generation ---
        transform_proposals = []
        for source_name, fields in source_profiles.items():
            field_dicts = [
                {"name": f.name, "type": f.inferred_type, "values": f.sample_values}
                for f in fields
            ]
            transforms = self._transform_generator.generate_transforms(
                request=request,
                source_name=source_name,
                fields=field_dicts,
            )
            transform_proposals.extend(transforms)
        all_proposals.extend(transform_proposals)

        # --- Aggregate ---
        auto_approved = sum(1 for p in all_proposals if p.review_status == ReviewStatus.AUTO_APPROVED)
        pending_review = sum(1 for p in all_proposals if p.review_status == ReviewStatus.PENDING_REVIEW)
        rejected = sum(1 for p in all_proposals if p.review_status == ReviewStatus.REJECTED_BY_HITL)

        return OrchestratorResult(
            request=request,
            source_proposals=[p.summary() for p in source_proposals],
            mapping_proposals=[p.summary() for p in mapping_proposals],
            join_proposals=[p.summary() for p in join_proposals],
            transform_proposals=[p.summary() for p in transform_proposals],
            all_proposals=[p.summary() for p in all_proposals],
            raw_proposals=all_proposals,
            auto_approved_count=auto_approved,
            pending_review_count=pending_review,
            rejected_count=rejected,
        )

    def orchestrate_simple(self, request: str) -> OrchestratorResult:
        """
        Simplified orchestration that works with just a request string.
        Uses the default catalog and runs only source identification.
        Full orchestration (with mappings, joins, transforms) requires
        source profiles and join candidates — call orchestrate() for that.
        """
        return self.orchestrate(request)

    def get_proposals_for_ir(self, proposals: list[Proposal]) -> list:
        """
        Extract IR operators from approved/auto-approved proposals.
        Only proposals with review_status APPROVED or AUTO_APPROVED contribute
        their IR operators to the pipeline. This is the bridge between the
        agent's proposals and the executable PipelineIR.
        """
        ir_operators = []
        for p in proposals:
            if p.review_status in (ReviewStatus.APPROVED, ReviewStatus.AUTO_APPROVED):
                if p.ir_operator is not None:
                    ir_operators.append(p.ir_operator)
        return ir_operators