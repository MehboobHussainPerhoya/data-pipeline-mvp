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


## FSD-Extension Phase 7 — Ontology / Common Model Layer
- BusinessObjectType references an existing SchemaDefinition by reference,
  not by copying fields — 5 object types defined: SupportCase, KnowledgeArticle,
  Customer, Product, Transaction. Product's identity field is explicitly
  product_variation_id, not product_id (FR-ONT-01).
- derive_relationships_from_ir() builds Relationship objects directly from
  JoinOperator/JoinKeyPair data already in the IR — never redefines join
  logic separately, so relationships can't drift from the actual pipeline
  (FR-ONT-02). Provenance (source_origin, confidence, review_status) is
  pulled from the operator, same pattern as Phase 6's lineage tracker.
- FR-ONT-03 (highest-priority requirement this phase) enforced structurally:
  OntologyMapper takes read-only record/IR snapshots, has no write/mutate/
  deploy method, and returns ir_unchanged=True/output_unchanged=True flags.
  Verified via before/after hash comparison in test_phase7.py, not just
  a self-reported flag.
- Verified: Transaction->Product relationship correctly shows
  left_key=product_id (the real FK name on the transactions side),
  right_key=product_variation_id (the corrected join target) — matches
  FSD Appendix A's human-corrected join, source_origin=human_override.


## FSD-Extension Phase 8 — Versioning / Proposal & Diff System
- Branch-based editing: BranchStore deep-copies a PipelineIR into an isolated
  named branch; editing a branch never touches Main until an explicit merge
  (FR-VER-01).
- Operator-level diffing: diff_pipeline_irs() matches operators by output
  name; added/removed/modified detected per-operator, with Join operators
  specifically decoding which mapping entries changed (e.g. "Product
  inquiry": "ORDER" -> "PRODUCT") for human-readable review (FR-VER-02).
- Propose/review/merge: VersionProposal freezes the diff at creation time;
  review requires reviewer != proposer (second-party review enforced, not
  just suggested); merge requires APPROVED status (FR-VER-03).
- Rollback: VersionHistory snapshots Main pre/post every merge; rollback_to()
  reverts and records the rollback itself as a new snapshot for audit
  (FR-VER-04).
- Hard separation maintained: merge_proposal() only calls
  branch_store.set_main() — changes pipeline LOGIC only. It never calls
  run_pipeline(), validate_pipeline(), or deploy_pipeline(). Deploying
  output after a merge still requires the full separate
  run -> validate -> deploy(approved=True) sequence through deploy_gate.py,
  unchanged.
- Standing rule from Phase 7's bug applied throughout: no function returns
  None for a not-found case — get_branch()/get_proposal() raise KeyError,
  diff_pipeline_irs() raises ValueError on None input.

git add . && git commit -m "Phase 8 complete: versioning/branch/diff/proposal/rollback, merge-vs-deploy separation verified" && git push


## FSD-Extension Phase 9 — Deployment Module (FSD 4.14)
- FR-DEPLOY-01 (explicit deploy action) and FR-DEPLOY-03 (deployment
  pre-checks) confirmed already satisfied by existing deploy_gate.py —
  its is_safe=True AND approved=True requirement is already an explicit,
  separate action gated on validation passing. No new code written for
  these two requirements; cited exact existing code in the state-confirmation
  report.
- FR-DEPLOY-02 (scheduled & triggered builds) implemented as new
  src/deployment/scheduler.py: BuildScheduler supports cron-like scheduled
  builds (check_schedules) and event-triggered builds (trigger_event).
  A build = run pipeline + validate. A build NEVER deploys on its own.
- Critical invariant enforced structurally: BuildScheduler has NO deploy
  method and NO import of deploy_gate. A scheduled/triggered build produces
  a BuildResult (candidate output) that must still pass through
  deploy_gate.py approval gate (is_safe=True AND approved=True) before
  anything is actually deployed. Scheduling automates the build, never the
  approval. Verified by test_scheduler_has_no_deploy_method and
  test_scheduler_does_not_import_deploy_gate (both assert on actual
  structure/source, not docstrings).
- deploy_gate.py extension (the ONLY change to that file): added two
  optional parameters (version_ref, deployed_by) recorded in the audit
  log for traceability. The core gating logic (the two if-not blocks)
  is byte-for-byte identical before and after. When the new params are
  None (the defaults), the audit log format is character-for-character
  identical to the previous format — fully backward compatible.
- server.py: deploy() tool extended to pass version_ref and deployed_by
  through to deploy_pipeline(). 12 new scheduler MCP tools added
  (scheduler_add_schedule, scheduler_add_event_trigger,
  scheduler_check_schedules, scheduler_trigger_event,
  scheduler_trigger_manual, scheduler_get_build_results,
  scheduler_get_latest_build, scheduler_list_schedules,
  scheduler_list_event_triggers, scheduler_enable_schedule,
  scheduler_disable_schedule, scheduler_summary).
- Cron expressions: 5-field format (minute hour dom month dow), each field
  supports * (any), single number, or comma-separated numbers. Deliberately
  minimal — no third-party cron library. Range/step syntax could be added
  later if needed.
- AUDIT FINDING: the edit_existing_file tool introduced a typo
  (output_path:.str with a stray colon) into deploy_gate.py during the
  initial edit. Caught immediately by re-reading the file after the edit,
  fixed with a single_find_and_replace before proceeding. This reinforces
  the standing rule: always re-read a file after editing it, never trust
  the tool success message.

  
## FSD-Extension Phase 10 — Monitoring & Alerting
- FR-MON-01: run health dashboard reads from BuildScheduler's BuildResult
  history (Phase 9) and deploy_audit_log.txt — no parallel run-tracking
  system invented. Exposes status, duration, row-count trends.
- FR-MON-02: data-quality metrics (null-rate, duplicate-rate, schema-drift)
  computed from real output records. Schema-drift compares against the
  actual Schema Registry (Phase 1) as baseline — three drift types
  detected: missing_field, extra_field, type_mismatch.
- FR-MON-03 (priority requirement this phase): AlertEngine generates real
  AlertRecord objects (owner, reason, timestamp, severity) for SLA breaches
  and run/build failures. Notification channel is an explicitly-labeled
  stub (in-memory/file) — no fake email/Slack integration, per FSD MVP
  scope. 15 tests cover this requirement specifically, including
  no-false-positive cases and idempotency.
- Confirmed observational-only: monitoring imports only from
  deployment.scheduler and schema.registry_setup — no import of
  deploy_gate.py, no deploy/build-trigger methods. deploy_gate.py diff
  confirmed genuinely empty (not just "only cache files").
- Verified: 64/64 real pytest output (31 new + 27 Phase 9 + 6 original,
  no regressions), all files parse clean, both artifact greps empty.


## FSD-Extension Phase 11 — Security & Access Control
- FR-SEC-01: RBACManager with 5 roles (viewer, editor, approver, deployer,
  admin), each an explicit permission set — deployer does NOT imply
  approve, approver does NOT imply deploy (confirmed by dedicated tests).
  require_permission decorator wraps MCP tools including deploy,
  versioning_update_branch_ir, versioning_create/review/merge_proposal,
  versioning_rollback.
- FR-SEC-02: column-level security ties into the Schema Registry via a new
  `sensitive: bool` flag on FieldDefinition (additive) — ColumnSecurityManager
  filters sensitive fields by role, default visible only to approver/admin.
- FR-SEC-03: unified AuditLogger (JSONL) wraps/fills gaps in existing
  Phase 6/8/9 logs rather than duplicating them — logs actor for edits,
  approvals, deploys, access-denials, role assignments.
- Two-layer independence explicitly proven: RBAC sits in front of
  deploy_gate.py as a stricter, additive precondition — a viewer is blocked
  by AccessDeniedError before ever reaching deploy_gate.py, and separately
  a deployer with approved=False is still blocked by deploy_gate.py's own
  RuntimeError. Both directions tested; deploy_gate.py's diff confirmed
  genuinely empty, verified directly, not just claimed.
- Verified: 132/132 real pytest output (68 new + 64 prior, zero
  regressions), all files parse clean, both artifact greps empty.


## FSD-Extension Phase 12 — Ingestion & Normalization Enhancements (FINAL PHASE)
- FR-ING-03: HighWaterMarkTracker enables incremental/CDC-style ingestion
  via persisted marks + filter helpers.
- FR-ING-05 (priority gap, now fixed): ingestion failures are isolated
  per-source via ingest_with_isolation()/run_ingestion_isolated() —
  one source failing (e.g. API connection refused) no longer crashes the
  whole run; other sources' data still flows through, with per-source
  success/failure recorded.
- FR-NORM-02/04: TransformChain compiles to existing IR MapOperators (no
  parallel transform system) with per-step preview via preview_at_step().
- FR-UNION-01/03: check_schemas_match() raises before execution on schema
  mismatch; detect_duplicates() flags exact-duplicate rows across unioned
  sources without silently removing them.
- Verified: 161/161 tests, deploy_gate.py diff empty (touched exactly once
  across all 12 phases — Phase 9's additive parameters — never weakened),
  full-codebase artifact scan clean.

## PROJECT STATUS: 12-phase FSD extension complete
Went from a 3-phase manual MVP (proven with a real agent via MCP) to full
FSD coverage, phase by phase, each independently verified rather than
trusted on the implementing agent's self-report — catching and fixing
real bugs along the way (Phase 3 scratchpad contamination + missing
import, Phase 5 stalled-session corruption + test crash, Phase 7 silent
relationship-resolution bug, Phase 9 scratchpad contamination in
deploy_gate.py itself). The one constant throughout: deploy_gate.py's
core is_safe-AND-approved gate, with no bypass, verified unchanged at
every single phase.

Known remaining gaps (honest, not exhaustive): stub notification channel
(no real email/Slack), non-git-backed branching, Python-only execution
engine (no Spark/Flink), no RDBMS/Kafka connectors, no secrets manager,
heuristic (not ML-trained) agent confidence scoring, no streaming
ingestion, no round-trip IR editing. All were either explicitly
out-of-scope per the FSD or reasonable MVP-level simplifications.
