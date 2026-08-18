# Decisions Log — data-pipeline-mvp

## Phase 1 — Normalization decisions (original MVP)
- `created_at` for support_tickets mapped from `First Response Time`
  (source has no literal "ticket created" timestamp; `Date of Purchase`
  refers to the product, not the ticket).
- `subject` and `channel` on SupportCase changed from required to Optional
  in canonical.py — real ticket data can have blanks in these fields.
- case_id collision discovered: tickets and support_activity_api both use
  small sequential integers (1, 2, 3...), causing 200 duplicate case_ids
  after union. Fixed by prefixing case_id with source name
  ("ticket_<id>" / "api_<id>") in both mapper functions.

## Phase 2 — Join decisions (original MVP)
- No shared key exists between SupportCase and KnowledgeArticle (independent
  datasets). Join implemented as content-based: ticket_type -> kb category,
  via manual mapping (TICKET_TYPE_TO_KB_CATEGORY in transform/join_config.py).
- 2 of 5 ticket_types map strongly (Refund request->REFUND, Cancellation
  request->CANCEL); 3 are weak/approximate best-guess proxies (Billing
  inquiry->PAYMENT, Product inquiry->ORDER, Technical issue->CONTACT) since
  no closer equivalent exists in the KB category vocabulary.
- Join built as a generic function taking the mapping as a parameter, not
  hardcoded, so it can become an MCP 'run_join' tool later.

## Phase 3 — Validation & safety decisions (original MVP)
- Final output contract (JoinedCaseOutput) is stricter than intermediate
  SupportCase: subject required in final output even though optional mid-pipeline.
- "Safe to deploy" = zero output-validation errors AND zero duplicate case_ids
  AND non-empty output. Any violation blocks deploy entirely (no partial deploy).
- deploy_pipeline() is the single choke point for all output — requires both
  is_safe=True and approved=True, with no bypass. This shape carries forward
  unchanged into every later phase and every MCP tool built on top of it.
- Every successful deploy is appended to deploy_audit_log.txt for traceability.

## Decision: Extend vs. Rebuild for full FSD compliance
- Gap analysis (via GLM/Continue) showed the existing layer structure
  (ingestion -> normalization -> transform -> validation -> MCP) already
  matches the FSD's architecture, and deploy_gate.py's approval gate was
  already proven working with a real agent.
- Decision: EXTEND the existing MVP, not rebuild from scratch. Rationale:
  rebuilding would discard a validated safety mechanism to rebuild an
  architecture that was already correctly shaped; every missing FSD
  component (Agent Orchestrator, IR, Ontology, Versioning, Provenance,
  Monitoring) is new code added alongside the existing structure either way.
- Hard ordering constraint imposed: Schema Registry -> IR -> Type Checker/
  Contract Validator -> Agent Orchestrator. The orchestrator's job is to
  produce/consume IR objects, so it cannot be built before the IR exists
  without requiring a rewrite.

## FSD-Extension Phase 1 — Schema & Type Registry
- Replaced two hardcoded Pydantic models with an extensible, versioned
  TypeRegistry (7 built-in canonical types, type-scoped operations,
  custom-type registration, version snapshots).
- SupportCase, KnowledgeArticle, JoinedCaseOutput re-registered into the
  registry as SchemaDefinitions — existing Pydantic models unchanged.
- FR-VAL-01 (required-column tracking) partially satisfied here as a
  foundation; full live enforcement deferred to Phase 3.

## FSD-Extension Phase 2 — Intermediate Representation (IR)
- Defined 7 canonical operators (Cast, Map, Filter, Join, Aggregate, Window,
  Union) as structured, serializable Pydantic models.
- run_pipeline() in server.py refactored to compile and execute from a
  PipelineIR object instead of imperative Python calls.
- Verified IR executor output is byte-for-byte identical to the old
  imperative code (8,669 rows, 0 errors) before accepting the refactor.
- Spark/Flink compilation deferred; Python executor only for now.
- Round-trip IR editing (FR-IR-04) explicitly deferred as a "Could-have."

## FSD-Extension Phase 3 — Type Checker & Contract Validator
- TypeChecker statically validates a PipelineIR before execution (bad input
  refs, invalid types, invalid join types, invalid agg functions, unknown
  transforms all caught with per-operator error messages).
- ContractValidator checks required-column satisfaction and detects
  breaking changes (a required column silently dropped from the contract).
- is_safe_to_deploy() refactored to use these internally, but its
  (bool, list[str]) return shape preserved unchanged so deploy_gate.py
  did not need to change.
- AUDIT FINDING: found and fixed AI-conversation scratchpad text
  accidentally left inside output_validator.py and canonical.py (causing
  SyntaxErrors), and a missing FastMCP import in server.py. Root cause:
  Continue's file-edit tool was silently leaving scratchpad text in files.
  This established the standing rule: every phase must be independently
  re-verified (pytest, artifact grep, git diff, live Inspector run) rather
  than trusting the agent's own "complete" report.

## FSD-Extension Phase 4 — AI Agent Orchestrator
- Every agent decision is a structured Proposal object (type, confidence
  0-1, human-readable rationale, evidence dict, review_status,
  auto_eligible, ir_operator) — never a bare dict.
- Confidence thresholds fixed by the FSD, not agent judgment: mapping and
  transform proposals >=0.85 may auto-apply; join proposals ALWAYS route
  to HITL review regardless of score.
- Join inference weights uniqueness_ratio at 0.35 (the heaviest signal)
  specifically because a non-unique key produces row-count skew that type
  checking alone cannot catch — this directly encodes the product_id vs.
  product_variation_id lesson from the companion CLV pipeline (FSD Section 7).
- Verified: orchestrator correctly ranks product_variation_id (unique,
  confidence 0.85) above product_id (non-unique, confidence 0.61, flagged
  with an explicit row-count-skew warning) on real data — not hardcoded to
  this one example, computed from uniqueness/value-overlap signals.

## FSD-Extension Phase 5 — Preview & HITL Gate Enhancement
- Added hitl_gate.py: a proposal-level review gate upstream of deploy_gate.py
  (which remains completely untouched — confirmed via empty git diff after
  every phase). Supports list/approve/reject/bulk-approve/bulk-reject.
- AuditEntry records reviewer identity, timestamp, and proposal summary for
  every review decision — a separate trail from the existing deploy audit
  log, persisted to disk (data/processed/hitl_audit_trail.json).
- AUDIT FINDINGS during this phase: (1) mapping_inference.py had a NameError
  bug (self._is_int/self._is_float called inside a module-level function
  where self doesn't exist) — fixed. (2) server.py's Phase 4 MCP stub tools
  (agent_get_pending_reviews, agent_review_proposal) were broken — wrong
  cache keys, dict/object mismatch — fixed. (3) A GLM session stalled mid-
  Phase-5 and left literal AI-scratchpad text inside orchestrator.py and a
  syntax error (stray trailing quote) in server.py; a first "Phase 5
  complete" report falsely claimed these were clean. Caught only by running
  the artifact grep and Inspector manually instead of trusting the report.
  (4) This same scratchpad-injection bug had, at some point, overwritten
  this entire decisions_log.md file with tool scratchpad text — the log was
  reconstructed from conversation history after the fact.
- Standing rule reinforced: GLM/Continue's terminal tool in this environment
  cannot reliably surface command output back to the agent itself — so the
  agent cannot be trusted to self-verify. All verification (pytest, artifact
  grep, ast.parse, git diff, live mcp dev Inspector run) must be run and read
  by the user directly, not delegated.

  
## FSD-Extension Phase 6 — Provenance & Lineage Tracking
- LineageTracker hooks into the IR executor (src/ir/executor.py via
  attach_lineage_tracker()) and records a LineageRecord after every
  operator execution: input dataset/fields -> output dataset/field,
  operator type, and parameters (FR-PROV-01).
- For Join operators specifically, the tracker pulls source_origin,
  confidence, review_status, and rejection_reason directly from the
  JoinOperator itself (not re-derived), since JoinInference (Phase 4)
  already sets these fields when creating a join from a Proposal
  (FR-PROV-02).
- Both the rejected product_id join (confidence 0.61, non-unique) and the
  approved product_variation_id join (confidence 0.85, unique) are
  retained in the lineage store — not just the winner — satisfying the
  FSD's audit requirement to keep rejected proposals visible.
- LineageQuery.trace_field()/explain_field() answer "why does this output
  exist" by walking the stored records, without re-running the pipeline
  (FR-PROV-03).
- Verified: 9/9 phase6 smoke tests passed, including the CLV scenario test
  proving product_variation_id ranks above product_id with the correct
  confidence scores and provenance retained for both.
- Note: 3 files (lineage_store.py, lineage_tracker.py, query.py) were saved
  with a UTF-8 BOM, which breaks plain ast.parse() calls without an
  explicit encoding='utf-8' argument — cosmetic, not a functional bug,
  confirmed by both test_phase6.py and pytest running these files
  successfully.
