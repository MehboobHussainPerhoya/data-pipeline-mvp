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
from ir.pipeline_definition import support_case_pipeline_ir
from ontology.object_types import OBJECT_TYPE_REGISTRY, get_object_type
from ontology.relationships import derive_relationships_from_ir
from ontology.mapper import OntologyMapper
from versioning.branch import BranchStore
from versioning.diff import diff_pipeline_irs
from versioning.proposal import ProposalStore, ProposalStatus
from versioning.rollback import VersionHistory
from deployment.scheduler import BuildScheduler

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

# ---------------------------------------------------------------------------
# Phase 8: Versioning / Proposal & Diff System (FSD 4.13)
# Branch-based editing, diffing, propose/review/merge, rollback.
# These manage PIPELINE LOGIC changes only — they do NOT deploy output.
# Deploying output still requires deploy_gate.py (is_safe=True AND approved=True).
# Merge and deploy are distinct actions by design.
# ---------------------------------------------------------------------------
_branch_store = BranchStore(main_ir=support_case_pipeline_ir)
_proposal_store = ProposalStore(branch_store=_branch_store)
_version_history = VersionHistory(branch_store=_branch_store)
_version_history.snapshot_initial()  # capture the starting Main as version 1

# ---------------------------------------------------------------------------
# Phase 9: Build Scheduler (FSD 4.14, FR-DEPLOY-02)
# Scheduled (cron-like) and event-triggered BUILDS only — never deploys.
# A scheduled/triggered build produces a candidate output that must still
# pass through deploy_gate.py (is_safe=True AND approved=True) before
# anything is actually deployed. Scheduling automates the build, never the
# approval.
# ---------------------------------------------------------------------------
_build_scheduler = BuildScheduler()


def _run_pipeline_for_scheduler() -> tuple[list[JoinedCaseOutput], list[tuple]]:
    """
    Build function for the scheduler — runs the full pipeline and returns
    (output_records, errors). This is what scheduled/triggered builds execute.
    It does NOT deploy — the scheduler validates the result and stores it as
    a candidate for later explicit deployment.
    """
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

    # Cache the result so validate_pipeline/deploy can use it after a build
    _cache["output_records"] = output_records
    _cache["errors"] = errors

    return output_records, errors


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
def deploy(approved: bool, deployed_by: str = "", version_ref: str = "") -> dict:
    """Deploys the final output. WRITE ACTION — requires validate_pipeline()
    to have passed AND approved=True to be explicitly set by the caller.
    There is no bypass: missing either condition blocks the deploy.

    deployed_by: identity of the person approving the deploy (for audit log).
    version_ref: the version of the pipeline being deployed (for audit log).
    Both are optional metadata — they do not affect the gating condition."""
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
        version_ref=version_ref if version_ref else None,
        deployed_by=deployed_by if deployed_by else None,
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


# ---------------------------------------------------------------------------
# Phase 7: Common Model / Ontology Layer MCP tools (FSD 4.12)
# These tools are strictly READ-ONLY over pipeline output and IR (FR-ONT-03).
# ---------------------------------------------------------------------------

_ontology_mapper = OntologyMapper()


@mcp.tool()
def ontology_list_object_types() -> list[dict]:
    """Lists all defined business object types in the ontology layer (FR-ONT-01).
    Each type references a SchemaDefinition from the Schema Registry — fields
    are not redefined here."""
    return [obj_type.to_dict() for obj_type in OBJECT_TYPE_REGISTRY.values()]


@mcp.tool()
def ontology_map_records(object_type_name: str) -> dict:
    """Maps the cached pipeline output records to a business object type (FR-ONT-01).
    Also derives relationships from the pipeline IR's Join operators (FR-ONT-02).
    This is a READ-ONLY annotation over existing output — it does not mutate
    the output, the IR, or trigger re-execution (FR-ONT-03).
    Must be called after run_pipeline()."""
    if "output_records" not in _cache:
        raise RuntimeError("No pipeline result cached — call run_pipeline() first.")

    result = _ontology_mapper.map_records(
        records=_cache["output_records"],
        object_type_name=object_type_name,
        ir=support_case_pipeline_ir,
    )
    return result.to_dict()


@mcp.tool()
def ontology_get_relationships() -> list[dict]:
    """Derives relationships between object types from the pipeline IR's Join
    operators (FR-ONT-02). Each relationship records the exact join key pair
    that produced it — relationships are not invented separately.
    This is a READ-ONLY operation over the IR (FR-ONT-03)."""
    relationships = derive_relationships_from_ir(support_case_pipeline_ir)
    return [rel.to_dict() for rel in relationships]


# ---------------------------------------------------------------------------
# Phase 8: Versioning / Proposal & Diff System MCP tools (FSD 4.13)
# ---------------------------------------------------------------------------

@mcp.tool()
def versioning_list_branches() -> list[dict]:
    """Lists all branches (not including Main) with their summaries (FR-VER-01).
    Each branch is an isolated copy of a PipelineIR that can be edited
    without affecting the live Main version."""
    return [b.summary() for b in _branch_store.list_branches()]


@mcp.tool()
def versioning_get_main() -> dict:
    """Returns a summary of the current Main pipeline IR (FR-VER-01).
    This is the live pipeline definition that runs when run_pipeline() is called."""
    return _branch_store.get_main().to_summary()


@mcp.tool()
def versioning_create_branch(branch_name: str, from_branch: str = "Main") -> dict:
    """Creates a new branch as an isolated copy of Main (or another branch) (FR-VER-01).
    Edits to the branch do not affect Main until merged via the proposal workflow.
    branch_name: the name for the new branch (cannot be 'Main').
    from_branch: the source to copy from (default 'Main')."""
    branch = _branch_store.create_branch(name=branch_name, from_branch=from_branch)
    return branch.summary()


@mcp.tool()
def versioning_get_branch(branch_name: str) -> dict:
    """Returns a summary of a specific branch including its pipeline IR (FR-VER-01).
    Raises an error if the branch does not exist."""
    branch = _branch_store.get_branch(name=branch_name)
    return {**branch.summary(), "ir_summary": branch.ir.to_summary()}


@mcp.tool()
def versioning_get_branch_ir(branch_name: str) -> dict:
    """Returns the full PipelineIR of a branch as JSON for inspection or editing (FR-VER-01).
    The returned IR can be modified and saved back via versioning_update_branch_ir."""
    branch = _branch_store.get_branch(name=branch_name)
    return {"branch_name": branch_name, "ir_json": branch.ir.to_json()}


@mcp.tool()
def versioning_update_branch_ir(branch_name: str, ir_json: str) -> dict:
    """Replaces a branch's PipelineIR with a new version provided as JSON (FR-VER-01).
    This is how edits to a branch are saved. The branch must exist and not be merged.
    ir_json: a JSON string representing the new PipelineIR."""
    from ir.pipeline_ir import PipelineIR
    new_ir = PipelineIR.model_validate_json(ir_json)
    branch = _branch_store.update_branch(name=branch_name, ir=new_ir)
    return branch.summary()


@mcp.tool()
def versioning_diff_branch(branch_name: str) -> dict:
    """Shows an explicit operator-level diff between a branch and Main (FR-VER-02).
    Reports added, removed, and modified operators and input sources.
    This is a structural diff, not a text diff — it shows which Join/Cast/Map/etc.
    steps changed, in a form a human reviewer can read."""
    branch = _branch_store.get_branch(name=branch_name)
    diff = diff_pipeline_irs(_branch_store.get_main(), branch.ir)
    return diff.summary()


@mcp.tool()
def versioning_create_proposal(proposer: str, branch_name: str) -> dict:
    """Creates a proposal to merge a branch into Main (FR-VER-03).
    The diff is computed and frozen at proposal time so the reviewer sees
    exactly what was proposed. The proposal starts in 'open' status.
    proposer: identity of who is proposing the merge.
    branch_name: the branch to merge."""
    proposal = _proposal_store.create_proposal(proposer=proposer, branch_name=branch_name)
    return proposal.summary()


@mcp.tool()
def versioning_list_proposals(status: str = None) -> list[dict]:
    """Lists all proposals, optionally filtered by status (FR-VER-03).
    status: if provided, one of 'open', 'approved', 'rejected', 'merged', 'superseded'."""
    if status:
        status_enum = ProposalStatus(status)
    else:
        status_enum = None
    return [p.summary() for p in _proposal_store.list_proposals(status=status_enum)]


@mcp.tool()
def versioning_review_proposal(proposal_id: int, reviewer: str, action: str, reason: str = "") -> dict:
    """Reviews a proposal — approves or rejects it (FR-VER-03).
    Enforces second-party review: reviewer MUST differ from the proposer.
    proposal_id: the proposal to review.
    reviewer: identity of the reviewer (must be different from the proposer).
    action: 'approve' or 'reject'.
    reason: optional reason (required if rejecting)."""
    proposal = _proposal_store.review_proposal(
        proposal_id=proposal_id,
        reviewer=reviewer,
        action=action,
        reason=reason,
    )
    return proposal.summary()


@mcp.tool()
def versioning_merge_proposal(proposal_id: int) -> dict:
    """Merges an approved proposal's branch into Main (FR-VER-03).
    The proposal must have been approved by a reviewer distinct from the proposer.

    IMPORTANT: This is a PIPELINE LOGIC change only — it does NOT deploy output.
    After merging, the user must still run_pipeline() and deploy() through
    deploy_gate.py (is_safe=True AND approved=True) to produce and deploy output.
    Merge and deploy are distinct actions by design."""
    proposal = _proposal_store.get_proposal(proposal_id)
    branch_name = proposal.branch_name

    # Snapshot the current Main BEFORE the merge replaces it
    current_main = _branch_store.get_main()
    _version_history.snapshot_pre_merge(previous_main_ir=current_main, branch_name=branch_name)

    # Perform the merge (replaces Main with the branch's IR)
    proposal = _proposal_store.merge_proposal(proposal_id=proposal_id)

    # Snapshot the new Main AFTER the merge
    _version_history.snapshot_post_merge(branch_name=branch_name)

    return proposal.summary()


@mcp.tool()
def versioning_list_versions() -> list[dict]:
    """Lists all version snapshots of Main (FR-VER-04).
    Each snapshot captures Main's PipelineIR at a point in time — initial,
    pre-merge, post-merge, or rollback."""
    return [s.summary() for s in _version_history.list_versions()]


@mcp.tool()
def versioning_rollback(version: int) -> dict:
    """Reverts Main to a previously deployed version (FR-VER-04).
    This is a PIPELINE LOGIC change only — it does NOT deploy output.
    After rollback, the user must still run_pipeline() and deploy() to
    produce output from the reverted logic.
    version: the version number to revert to (see versioning_list_versions)."""
    snapshot = _version_history.rollback_to(version=version)
    return snapshot.summary()


# ---------------------------------------------------------------------------
# Phase 9: Build Scheduler MCP tools (FSD 4.14, FR-DEPLOY-02)
# Scheduled (cron-like) and event-triggered BUILDS only — never deploys.
# ---------------------------------------------------------------------------

@mcp.tool()
def scheduler_add_schedule(name: str, cron_expression: str) -> dict:
    """Adds a cron-like scheduled build (FR-DEPLOY-02).
    The build runs the pipeline and validates the result — it does NOT deploy.
    Deploying the output still requires a separate deploy() call with approved=True.

    name: unique name for this schedule.
    cron_expression: 5-field cron expression (minute hour day-of-month month day-of-week).
    Each field: * (any), a single number, or comma-separated numbers (e.g., "0,30").
    Examples: "0 * * * *" (top of every hour), "30 9 * * 1-5" (9:30 Mon-Fri)."""
    job = _build_scheduler.add_schedule(
        name=name,
        cron_expression=cron_expression,
        build_fn=_run_pipeline_for_scheduler,
    )
    return job.summary()


@mcp.tool()
def scheduler_add_event_trigger(name: str, event_name: str) -> dict:
    """Adds an event-triggered build (FR-DEPLOY-02).
    When the event fires (via scheduler_trigger_event), the build runs the
    pipeline and validates the result — it does NOT deploy.
    Deploying the output still requires a separate deploy() call with approved=True.

    name: unique name for this trigger.
    event_name: the event that triggers the build (e.g., "data_arrived")."""
    trigger = _build_scheduler.add_event_trigger(
        name=name,
        event_name=event_name,
        build_fn=_run_pipeline_for_scheduler,
    )
    return trigger.summary()


@mcp.tool()
def scheduler_check_schedules() -> list[dict]:
    """Checks all scheduled builds and runs any that are due now (FR-DEPLOY-02).
    Returns a list of build results for any builds that were triggered.
    Builds that are disabled or not due are skipped.
    Triggered builds are NOT deployed — they produce candidate outputs only."""
    results = _build_scheduler.check_schedules()
    return [r.summary() for r in results]


@mcp.tool()
def scheduler_trigger_event(event_name: str) -> list[dict]:
    """Fires an event, triggering all builds subscribed to that event (FR-DEPLOY-02).
    Returns a list of build results for all builds that were triggered.
    Triggered builds are NOT deployed — they produce candidate outputs only.
    Deploying requires a separate deploy() call with approved=True.

    event_name: the event that occurred (e.g., "data_arrived")."""
    results = _build_scheduler.trigger_event(event_name)
    return [r.summary() for r in results]


@mcp.tool()
def scheduler_trigger_manual(name: str) -> dict:
    """Manually triggers a scheduled build by name, regardless of whether its
    cron schedule is due (FR-DEPLOY-02). This is for ad-hoc/test runs.
    The build is NOT deployed — it produces a candidate output only.
    Deploying requires a separate deploy() call with approved=True.

    name: the schedule to trigger manually."""
    result = _build_scheduler.trigger_manual(name)
    return result.summary()


@mcp.tool()
def scheduler_get_build_results() -> list[dict]:
    """Returns all build results from scheduled/triggered builds (FR-DEPLOY-02).
    These are candidate outputs — built and validated but NOT deployed.
    Deploying requires a separate deploy() call with approved=True."""
    return [r.summary() for r in _build_scheduler.get_build_results()]


@mcp.tool()
def scheduler_get_latest_build() -> dict:
    """Returns the most recent build result (FR-DEPLOY-02).
    This is a candidate output — built and validated but NOT deployed.
    Deploying requires a separate deploy() call with approved=True.
    Raises RuntimeError if no builds have run."""
    result = _build_scheduler.get_latest_build()
    if result is None:
        raise RuntimeError("No builds have been triggered — call scheduler_trigger_event or scheduler_check_schedules first.")
    return result.summary()


@mcp.tool()
def scheduler_list_schedules() -> list[dict]:
    """Lists all scheduled builds (FR-DEPLOY-02)."""
    return _build_scheduler.list_schedules()


@mcp.tool()
def scheduler_list_event_triggers() -> list[dict]:
    """Lists all event triggers (FR-DEPLOY-02)."""
    return _build_scheduler.list_event_triggers()


@mcp.tool()
def scheduler_enable_schedule(name: str) -> dict:
    """Enables a scheduled build (FR-DEPLOY-02).
    name: the schedule to enable."""
    job = _build_scheduler.enable_schedule(name)
    return job.summary()


@mcp.tool()
def scheduler_disable_schedule(name: str) -> dict:
    """Disables a scheduled build (FR-DEPLOY-02).
    name: the schedule to disable."""
    job = _build_scheduler.disable_schedule(name)
    return job.summary()


@mcp.tool()
def scheduler_summary() -> dict:
    """Returns a summary of the scheduler state — schedules, triggers, and
    total builds run (FR-DEPLOY-02)."""
    return _build_scheduler.summary()


if __name__ == "__main__":
    mcp.run()
