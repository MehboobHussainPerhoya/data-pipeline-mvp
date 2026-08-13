"""
Stage 4: Transform Generation.

Generates IR-level transform operations (Cast, Map, Filter) that implement
the user's stated intent. For example, if the request mentions "clean" or
"trim", this stage proposes a Map operator with the trim_whitespace transform.
If a field is a string but the canonical schema expects a timestamp, it
proposes a Cast operator.

Transform proposals >= 0.85 confidence auto-apply; else HITL.

FSD requirements:
- FR-AGENT-04 (transform generation)
- FR-AGENT-05 (confidence scoring)
- FR-AGENT-06 (explanation output)
"""

from .proposal import Proposal, ProposalType
from .confidence import score_transform, route_proposal
from ir.operators import CastOperator, MapOperator, FilterOperator
from schema.registry_setup import registry
from typing import Optional
import re


def _normalize_name(name: str) -> str:
    return re.sub(r'[\s_\-]+', '', name.lower())


def _name_similarity(a: str, b: str) -> float:
    na = _normalize_name(a)
    nb = _normalize_name(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    from difflib import SequenceMatcher
    return SequenceMatcher(None, na, nb).ratio()


class TransformGeneration:
    """
    Stage 4 of the agent orchestrator.

    Usage:
        generator = TransformGeneration()
        proposals = generator.generate_transforms(
            request="clean and join tickets with KB",
            source_name="support_tickets",
            fields=[{"name": "Ticket Subject", "type": "String", "values": [...]}, ...],
            canonical_schema_name="SupportCase",
        )
    """

    # Intent keywords -> transform names
    INTENT_MAP = {
        "clean": "trim_whitespace",
        "trim": "trim_whitespace",
        "normalize": "trim_whitespace",
        "parse": "parse_timestamp",
        "timestamp": "parse_timestamp",
        "date": "parse_timestamp",
    }

    def generate_transforms(
        self,
        request: str,
        source_name: str,
        fields: list[dict],
        canonical_schema_name: str = None,
    ) -> list[Proposal]:
        """
        Generate transform proposals based on the request and field profiles.

        fields: list of dicts with keys: name, type, values (sample values)

        Two kinds of transforms are generated:
        1. Intent-based: if the request mentions "clean"/"trim", propose
           trim_whitespace on string fields.
        2. Type-based: if a field's inferred type doesn't match the canonical
           type, propose a Cast.
        """
        proposals = []
        request_lower = request.lower()

        # --- Intent-based transforms ---
        for intent_keyword, transform_name in self.INTENT_MAP.items():
            if intent_keyword in request_lower:
                for field in fields:
                    if field["type"] == "String" and transform_name == "trim_whitespace":
                        proposal = self._make_transform_proposal(
                            source_name=source_name,
                            field_name=field["name"],
                            transform_name=transform_name,
                            request=request,
                            rationale_prefix=f"Request mentions '{intent_keyword}'",
                        )
                        proposals.append(proposal)
                    elif field["type"] == "String" and transform_name == "parse_timestamp":
                        # Only propose timestamp parsing for fields that look like timestamps
                        if self._looks_like_timestamp(field.get("values", [])):
                            proposal = self._make_transform_proposal(
                                source_name=source_name,
                                field_name=field["name"],
                                transform_name=transform_name,
                                request=request,
                                rationale_prefix=f"Request mentions '{intent_keyword}'",
                            )
                            proposals.append(proposal)

        # --- Type-based Cast transforms ---
        if canonical_schema_name:
            from schema.registry_setup import get_schema
            try:
                schema = get_schema(canonical_schema_name)
                for field in fields:
                    for canon_field in schema.fields:
                        sim = _name_similarity(field["name"], canon_field.name)
                        if sim > 0.7 and field["type"] != canon_field.canonical_type:
                            # Field name matches a canonical field but types differ -> propose Cast
                            proposal = self._make_cast_proposal(
                                source_name=source_name,
                                field_name=field["name"],
                                target_type=canon_field.canonical_type,
                                source_type=field["type"],
                                name_similarity=sim,
                            )
                            proposals.append(proposal)
                            break  # only one cast per source field
            except ValueError:
                pass  # schema not found — skip type-based transforms

        return proposals

    def _make_transform_proposal(
        self,
        source_name: str,
        field_name: str,
        transform_name: str,
        request: str,
        rationale_prefix: str,
    ) -> Proposal:
        """Create a Map transform proposal."""
        # Check if the transform is valid for the String type
        type_valid = True
        operation_valid = transform_name in registry.get_type("String").valid_operations

        confidence, evidence = score_transform(
            name_similarity=0.8,  # field name is known
            type_valid=type_valid,
            operation_valid=operation_valid,
        )

        output_name = f"{source_name}_{field_name}_{transform_name}"
        ir_operator = MapOperator(
            op="Map",
            input=source_name,
            output=output_name,
            transform=transform_name,
            params={"field": field_name},
            source="agent_inferred",
            confidence=confidence,
            review_status="unreviewed",
        )

        rationale = (
            f"{rationale_prefix} — apply '{transform_name}' to field '{field_name}' "
            f"in source '{source_name}'. Operation valid for String type: {operation_valid}."
        )

        proposal = Proposal(
            proposal_type=ProposalType.TRANSFORM,
            confidence=confidence,
            rationale=rationale,
            evidence={
                **evidence,
                "source_name": source_name,
                "field_name": field_name,
                "transform_name": transform_name,
            },
            ir_operator=ir_operator,
        )
        return route_proposal(proposal)

    def _make_cast_proposal(
        self,
        source_name: str,
        field_name: str,
        target_type: str,
        source_type: str,
        name_similarity: float,
    ) -> Proposal:
        """Create a Cast transform proposal."""
        type_valid = target_type in registry.type_names
        operation_valid = True  # Cast is always a valid operation if the target type exists

        confidence, evidence = score_transform(
            name_similarity=name_similarity,
            type_valid=type_valid,
            operation_valid=operation_valid,
        )

        output_name = f"{source_name}_{field_name}_cast"
        ir_operator = CastOperator(
            op="Cast",
            input=source_name,
            field=field_name,
            to=target_type,
            output=output_name,
            source="agent_inferred",
            confidence=confidence,
            review_status="unreviewed",
        )

        rationale = (
            f"Field '{field_name}' ({source_type}) matches canonical field "
            f"but types differ — propose Cast to {target_type}. "
            f"Name similarity: {name_similarity:.2f}."
        )

        proposal = Proposal(
            proposal_type=ProposalType.TRANSFORM,
            confidence=confidence,
            rationale=rationale,
            evidence={
                **evidence,
                "source_name": source_name,
                "field_name": field_name,
                "source_type": source_type,
                "target_type": target_type,
            },
            ir_operator=ir_operator,
        )
        return route_proposal(proposal)

    def _looks_like_timestamp(self, values: list) -> bool:
        """Check if any sample values look like timestamps."""
        for v in values[:20]:
            if v and isinstance(v, str) and re.match(r'\d{4}-\d{2}-\d{2}', v):
                return True
        return False