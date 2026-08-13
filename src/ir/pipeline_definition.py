"""
Pipeline definition — the IR for the current MVP support case pipeline.

This declaratively specifies what run_pipeline() used to do imperatively:
1. Ingest 3 sources (tickets CSV, KB CSV, API JSON)
2. Normalize each source to its canonical schema (Map operators)
3. Union tickets + API cases into one SupportCase dataset
4. Join cases to KB articles via ticket_type -> category mapping
5. Build output records (Map operator)

This PipelineIR object is what the executor runs and what the MCP server
exposes to the agent.
"""

from .pipeline_ir import PipelineIR, InputSource
from .operators import MapOperator, UnionOperator, JoinOperator, JoinKeyPair
from transform.join_config import TICKET_TYPE_TO_KB_CATEGORY

# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------
INPUTS = [
    InputSource(
        name="tickets_raw",
        source_type="csv",
        location="data/raw/support_tickets/customer_support_tickets.csv",
        schema_name="SupportCase",
        connector="read_support_tickets",
    ),
    InputSource(
        name="kb_raw",
        source_type="csv",
        location="data/raw/kb_articles/bitext_customer_support.csv",
        schema_name="KnowledgeArticle",
        connector="read_kb_articles",
    ),
    InputSource(
        name="api_raw",
        source_type="api",
        location="https://jsonplaceholder.typicode.com/todos",
        schema_name="SupportCase",
        connector="read_support_activity_api",
    ),
]

# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------
STEPS = [
    # Normalize each source to canonical schema
    MapOperator(
        op="Map",
        input="tickets_raw",
        output="tickets_normalized",
        transform="map_ticket_to_supportcase",
        source="manual",
    ),
    MapOperator(
        op="Map",
        input="kb_raw",
        output="kb_normalized",
        transform="map_kb_to_knowledgearticle",
        params={"needs_index": True},
        source="manual",
    ),
    MapOperator(
        op="Map",
        input="api_raw",
        output="api_normalized",
        transform="map_api_to_supportcase",
        source="manual",
    ),

    # Union tickets + API into one SupportCase dataset
    UnionOperator(
        op="Union",
        inputs=["tickets_normalized", "api_normalized"],
        output="all_cases",
        source="manual",
    ),

    # Join cases to KB articles via ticket_type -> category mapping
    JoinOperator(
        op="Join",
        left="all_cases",
        right="kb_normalized",
        on=[JoinKeyPair(
            left_key="ticket_type",
            right_key="category",
            mapping=TICKET_TYPE_TO_KB_CATEGORY,
        )],
        type="left",
        one_to_many=True,
        output="joined",
        source="manual",
        confidence=None,
        review_status="not_required",
    ),
]

# ---------------------------------------------------------------------------
# The complete pipeline IR
# ---------------------------------------------------------------------------
support_case_pipeline_ir = PipelineIR(
    name="support_case_pipeline",
    inputs=INPUTS,
    steps=STEPS,
    output_contract="JoinedCaseOutput",
)