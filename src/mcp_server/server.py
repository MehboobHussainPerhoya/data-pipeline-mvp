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


if __name__ == "__main__":
    mcp.run()