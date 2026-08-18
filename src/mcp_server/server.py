import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # so 'src' modules import cleanly

from ingestion.tickets_connector import read_support_tickets
from ingestion.kb_connector import read_kb_articles
from ingestion.api_connector import read_support_activity_api
from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase
from transform.join_engine import join_cases_to_articles
from transform.join_config import TICKET_TYPE_TO_KB_CATEGORY
from validation.output_validator import build_output_records, is_safe_to_deploy
from validation.deploy_gate import deploy_pipeline
from schema.canonical import SupportCase, KnowledgeArticle
from schema.output_schema import JoinedCaseOutput
from agent.orchestrator import AgentOrchestrator
from agent.hitl_gate import HITLGate
from provenance.lineage_tracker import LineageTracker
from provenance.query import LineageQuery

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    # mcp package not installed — server can't run, but imports don't crash
    FastMCP = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if FastMCP is not None:
    mcp = FastMCP("data-pipeline-mvp")

# In-memory cache — holds the result of the last run_pipeline call, so
# later tool calls (validate, preview, deploy) can build on it without
# re-running ingestion every time. A real backend would use a proper
# session/job store; this is deliberately the simplest thing that works.
_cache = {}

# Agent orchestrator instance — shared across tool calls
_orchestrator = AgentOrchestrator()


@mcp.tool()
def list_sources() -> list[dict]:
    """Lists the configured data sources for this pipeline (name, type, location)."""
    return [
        {"name": "support_tickets", "type": "csv", "location": "data/raw/support_tickets/customer_support_tickets.csv"},
        {"name": "kb_articles", "type": "csv", "location": "data/raw/kb_articles/bitext_customer_support.csv"},
        {"name": "support_activity_api", "type": "api", "location": "https://jsonplaceholder.typicode.com/todos"},
    ]


@mcp.tool()
def get_schema(model_name: str) -> dict:
    """Returns the JSON schema for a canonical model.
    model_name must be one of: SupportCase, KnowledgeArticle, JoinedCaseOutput."""
    models = {
        "SupportCase": SupportCase,
        "KnowledgeArticle": KnowledgeArticle,
        "JoinedCaseOutput": JoinedCaseOutput,
    }
    if model_name not in models:
        raise ValueError(f"Unknown model_name '{model_name}'. Must be one of: {list(models.keys())}")
    return models[model_name].model_json_schema()


@mcp.tool()
def run_pipeline() -> dict:
    """Runs ingestion, normalization, and join across all 3 sources.
    Caches the result for validate_pipeline/preview_output/deploy to use.
    Returns summary counts only — call preview_output to see actual records."""
    tickets_raw = read_support_tickets(str(PROJECT_ROOT / "data/raw/support_tickets/customer_support_tickets.csv"))
    kb_raw = read_kb_articles(str(PROJECT_ROOT / "data/raw/kb_articles/bitext_customer_support.csv"))
    api_raw = read_support_activity_api(
        fallback_path=str(PROJECT_ROOT / "data/sample/support_activity_api/sample.json")
    )

    cases = [map_ticket_to_supportcase(r) for r in tickets_raw] + \
            [map_api_to_supportcase(r) for r in api_raw]
    articles = [map_kb_to_knowledgearticle(r, i) for i, r in enumerate(kb_raw)]
    joined = join_cases_to_articles(cases, articles, TICKET_TYPE_TO_KB_CATEGORY)
    output_records, errors = build_output_records(joined)

    _cache["output_records"] = output_records
    _cache["errors"] = errors

    return {
        "cases_ingested": len(cases),
        "articles_ingested": len(articles),
        "output_records_built": len(output_records),
        "validation_errors": len(errors),
    }


@mcp.tool()
def validate_pipeline() -> dict:
    """Checks whether the last run_pipeline() result is safe to deploy.
    Must be called after run_pipeline()."""
    if "output_records" not in _cache:
        raise RuntimeError("No pipeline result cached — call run_pipeline() first.")

    is_safe, reasons = is_safe_to_deploy(_cache["output_records"], _cache["errors"])
    _cache["is_safe"] = is_safe
    _cache["safety_reasons"] = reasons
    return {"safe_to_deploy": is_safe, "reasons": reasons}


@mcp.tool()
def preview_output(limit: int = 10) -> list[dict]:
    """Returns the first `limit` deployable records for human/agent inspection.
    Must be called after run_pipeline()."""
    if "output_records" not in _cache:
        raise RuntimeError("No pipeline result cached — call run_pipeline() first.")
    return [r.model_dump() for r in _cache["output_records"][:limit]]


@mcp.tool()
def deploy(approved: bool) -> dict:
    """Deploys the final output. WRITE ACTION — requires validate_pipeline()
    to have passed AND approved=True to be explicitly set by the caller.
    There is no bypass: missing either condition blocks the deploy."""
    if "output_records" not in _cache:
        raise RuntimeError("No pipeline result cached — call run_pipeline() first.")
    if "is_safe" not in _cache:
        raise RuntimeError("Pipeline not validated — call validate_pipeline() first.")

    output_path = str(PROJECT_ROOT / "data/processed/support_cases_output.json")
    deploy_pipeline(
        _cache["output_records"],
        _cache["is_safe"],
        _cache["safety_reasons"],
        approved=approved,
        output_path=output_path,
    )
    return {"status": "deployed", "records": len(_cache["output_records"]), "path": output_path}


# ---------------------------------------------------------------------------
# Phase 4: AI Agent Orchestrator MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def agent_orchestrate(request: str) -> dict:
    """Runs the AI agent orchestrator on a natural-language request.
    Returns proposals for source identification, mapping, joins, and transforms.
    Each proposal has a confidence score, rationale, and review status.
    No proposals are executed — this is planning only."""
    result = _orchestrator.orchestrate_simple(request)
    # Store raw proposals and create a HITL gate for review tools
    _cache["last_orchestration"] = result
    _cache["hitl_gate"] = HITLGate(proposals=result.raw_proposals)
    return result.summary()


@mcp.tool()
def agent_get_pending_reviews() -> list[dict]:
    """Returns all proposals from the last orchestration that are pending
    human review (below the confidence threshold for auto-apply).
    This is the consolidated bulk review list (FR-PREV-03)."""
    gate = _cache.get("hitl_gate")
    if gate is None:
        return []
    return gate.list_pending_reviews()


@mcp.tool()
def agent_review_proposal(proposal_index: int, action: str, reviewer: str = "unknown", reason: str = "") -> dict:
    """Approves or rejects a pending agent proposal (FR-PREV-04).
    action must be 'approve' or 'reject'.
    reviewer: identity of the human reviewer (for audit trail).
    If rejecting, a reason must be provided."""
    gate = _cache.get("hitl_gate")
    if gate is None:
        raise RuntimeError("No orchestration result cached — call agent_orchestrate first.")

    if action == "approve":
        proposal = gate.approve(proposal_index=proposal_index, reviewer=reviewer)
    elif action == "reject":
        if not reason:
            raise ValueError("A reason must be provided when rejecting a proposal.")
        proposal = gate.reject(proposal_index=proposal_index, reviewer=reviewer, reason=reason)
    else:
        raise ValueError(f"Invalid action '{action}'. Must be 'approve' or 'reject'.")

    # Persist audit trail to disk (FR-PREV-04)
    audit_path = str(PROJECT_ROOT / "data/processed/hitl_audit_trail.json")
    gate.persist_audit_trail(audit_path)

    return {"proposal": proposal.summary(), "gate_summary": gate.summary()}


@mcp.tool()
def agent_get_audit_trail() -> list[dict]:
    """Returns the full HITL audit trail — every approval/rejection with
    reviewer identity, timestamp, and the reviewed proposal (FR-PREV-04)."""
    gate = _cache.get("hitl_gate")
    if gate is None:
        return []
    return gate.get_audit_trail()


@mcp.tool()
def agent_get_review_summary() -> dict:
    """Returns a summary of the current HITL gate state —
    total proposals, pending, approved, rejected, audit entries."""
    gate = _cache.get("hitl_gate")
    if gate is None:
        raise RuntimeError("No orchestration result cached — call agent_orchestrate first.")
    return gate.summary()


# ---------------------------------------------------------------------------
# Phase 6: Provenance & Lineage Tracking MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_lineage(dataset_name: str, field_name: str = None) -> dict:
    """Returns lineage information for an output dataset or specific field.
    Traces the full derivation chain back to source fields without re-running
    the pipeline (FR-PROV-03).
    dataset_name: the output dataset to trace.
    field_name: optional specific field to trace (if omitted, traces the whole dataset)."""
    tracker = _cache.get('lineage_tracker')
    if tracker is None:
        raise RuntimeError('No lineage data available - run a pipeline with lineage tracking first.')
    query = LineageQuery(tracker.store)
    if field_name:
        chain = query.trace_field(dataset_name, field_name)
        explanation = query.explain_field(dataset_name, field_name)
    else:
        chain = query.trace_dataset(dataset_name)
        explanation = f'Traced {len(chain)} lineage records for dataset {dataset_name}.'
    return {
        'records': [r.model_dump() for r in chain],
        'explanation': explanation,
        'record_count': len(chain),
    }


@mcp.tool()
def get_join_provenance() -> list[dict]:
    """Returns provenance for all join operations in the last pipeline run.
    For each join: the join rule, source (manual/agent/human), confidence score,
    and review status (FR-PROV-02)."""
    tracker = _cache.get('lineage_tracker')
    if tracker is None:
        raise RuntimeError('No lineage data available - run a pipeline with lineage tracking first.')
    query = LineageQuery(tracker.store)
    return query.get_join_provenance()


@mcp.tool()
def get_lineage_summary() -> dict:
    """Returns a summary of all lineage records from the last pipeline run."""
    tracker = _cache.get('lineage_tracker')
    if tracker is None:
        raise RuntimeError('No lineage data available - run a pipeline with lineage tracking first.')
    return tracker.summary()


if __name__ == "__main__":
    mcp.run()
