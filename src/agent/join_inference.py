"""
Stage 3: Join Inference.

Proposes join keys and join types between datasets using:
- Key name matching
- Type compatibility
- Value overlap (referential integrity check)
- Key uniqueness/cardinality analysis (THE critical check)

The uniqueness check is the core lesson from the companion CLV pipeline
(FSD Section 7, step 7): a join on a non-unique key produces row-count skew
that type checking cannot catch. This stage explicitly computes a
uniqueness_ratio for every candidate join key and factors it heavily into
the confidence score. A non-unique key gets a low confidence and a flagged
rationale, so it routes to HITL review rather than silently auto-applying.

FSD requirements:
- FR-AGENT-03 (join key inference)
- FR-AGENT-05 (confidence scoring)
- FR-AGENT-06 (explanation output)
"""

from .proposal import Proposal, ProposalType
from .confidence import score_join, route_proposal
from ir.operators import JoinOperator, JoinKeyPair
from typing import Optional
import re


def _normalize_name(name: str) -> str:
    return re.sub(r'[\s_\-]+', '', name.lower())


def _name_match(left_key: str, right_key: str) -> bool:
    """Check if two key names match (exact or normalized)."""
    return _normalize_name(left_key) == _normalize_name(right_key)


def _type_compatible(left_type: str, right_type: str) -> bool:
    """Check if two types are compatible for a join."""
    if left_type == right_type:
        return True
    # Integer <-> Double are comparable
    if {left_type, right_type} <= {"Integer", "Double"}:
        return True
    # Anything can be compared as String
    if left_type == "String" or right_type == "String":
        return True
    return False


def _value_overlap(left_values: list, right_values: list) -> float:
    """
    Compute what fraction of left key values exist in the right dataset.
    This is the referential integrity check — a good foreign key should
    have high overlap with the referenced table's primary key.
    Returns 0-1.
    """
    non_null_left = [v for v in left_values if v is not None and v != ""]
    if not non_null_left:
        return 0.0
    right_set = set(str(v) for v in right_values if v is not None and v != "")
    matching = sum(1 for v in non_null_left if str(v) in right_set)
    return matching / len(non_null_left)


def _uniqueness_ratio(values: list) -> float:
    """
    Compute how unique a set of values is.
    Returns 1.0 if all values are unique (perfect primary key).
    Returns 0.5 if each value appears exactly 2x.
    Returns ~0.0 if all values are the same.

    This is THE critical check from the CLV pipeline lesson:
    - product_id with uniqueness_ratio=0.34 -> flagged, low confidence
    - product_variation_id with uniqueness_ratio=1.0 -> high confidence
    """
    non_null = [v for v in values if v is not None and v != ""]
    if not non_null:
        return 0.0
    unique_count = len(set(non_null))
    total_count = len(non_null)
    return unique_count / total_count


class JoinKeyCandidate:
    """One candidate join key pair between two datasets."""

    def __init__(
        self,
        left_key: str,
        right_key: str,
        left_values: list,
        right_values: list,
        left_type: str = "String",
        right_type: str = "String",
    ):
        self.left_key = left_key
        self.right_key = right_key
        self.left_values = left_values
        self.right_values = right_values
        self.left_type = left_type
        self.right_type = right_type


class JoinInference:
    """
    Stage 3 of the agent orchestrator.

    Usage:
        inferrer = JoinInference()
        proposals = inferrer.infer_joins(
            left_name="transactions",
            right_name="products",
            candidates=[JoinKeyCandidate(...), ...],
        )
    """

    def infer_joins(
        self,
        left_name: str,
        right_name: str,
        candidates: list[JoinKeyCandidate],
        join_type: str = "left",
        output_name: str = "joined",
    ) -> list[Proposal]:
        """
        Propose join keys between two datasets.

        For each candidate key pair, compute:
        - name_match: do the key names match?
        - type_compatible: are the types compatible?
        - value_overlap: referential integrity (left keys in right?)
        - uniqueness_ratio: is the right key unique? (critical!)

        The uniqueness_ratio is weighted heavily in the confidence score.
        A non-unique key (uniqueness_ratio < 0.5) will always produce a
        low-confidence proposal that routes to HITL review.
        """
        proposals = []

        for candidate in candidates:
            nm = _name_match(candidate.left_key, candidate.right_key)
            tc = _type_compatible(candidate.left_type, candidate.right_type)
            vo = _value_overlap(candidate.left_values, candidate.right_values)
            ur = _uniqueness_ratio(candidate.right_values)

            confidence, evidence = score_join(
                name_match=nm,
                type_compatible=tc,
                value_overlap=vo,
                uniqueness_ratio=ur,
            )

            # Build the rationale — explicitly flag non-unique keys
            uniqueness_flag = ""
            if ur < 0.5:
                uniqueness_flag = (
                    f" WARNING: Right key '{candidate.right_key}' is NOT unique "
                    f"(uniqueness_ratio={ur:.2f}) — joining on this key will produce "
                    f"row-count skew. This is the exact failure mode from the CLV pipeline "
                    f"(FSD Section 7, step 7). Recommend reviewing before applying."
                )
            elif ur < 0.9:
                uniqueness_flag = (
                    f" NOTE: Right key '{candidate.right_key}' has moderate uniqueness "
                    f"(uniqueness_ratio={ur:.2f}) — some duplication may occur."
                )

            rationale = (
                f"Join {left_name}.{candidate.left_key} -> {right_name}.{candidate.right_key} "
                f"(type={join_type}). Name match: {nm}, type compatible: {tc}, "
                f"value overlap: {vo:.2f}, uniqueness: {ur:.2f}."
                f"{uniqueness_flag}"
            )

            # Build the IR JoinOperator that this proposal would add
            ir_operator = JoinOperator(
                op="Join",
                left=left_name,
                right=right_name,
                on=[JoinKeyPair(
                    left_key=candidate.left_key,
                    right_key=candidate.right_key,
                )],
                type=join_type,
                output=output_name,
                source="agent_inferred",
                confidence=confidence,
                review_status="unreviewed",
            )

            proposal = Proposal(
                proposal_type=ProposalType.JOIN,
                confidence=confidence,
                rationale=rationale,
                evidence={
                    **evidence,
                    "left_name": left_name,
                    "right_name": right_name,
                    "left_key": candidate.left_key,
                    "right_key": candidate.right_key,
                    "join_type": join_type,
                    "is_unique_key": ur >= 0.95,
                },
                ir_operator=ir_operator,
            )
            proposal = route_proposal(proposal)
            proposals.append(proposal)

        # Rank by confidence (highest first)
        proposals.sort(key=lambda p: p.confidence, reverse=True)
        return proposals