"""
Phase 8 smoke tests — Versioning / Proposal & Diff System (FSD 4.13).

Tests:
1. Editing a branch doesn't affect Main (FR-VER-01)
2. A diff between two real IRs correctly identifies added/modified/removed operators (FR-VER-02)
3. A merge is blocked without review (FR-VER-03)
4. A merge is blocked if reviewer == proposer (FR-VER-03)
5. A merge succeeds with valid distinct-reviewer approval (FR-VER-03)
6. Rollback restores a prior version correctly (FR-VER-04)
7. Merge does NOT deploy — merge and deploy are distinct actions (hard requirement)
8. get_branch raises a clear error for nonexistent branches (no silent None)
"""

import sys
import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ir.pipeline_ir import PipelineIR, InputSource
from ir.operators import (
    CastOperator, MapOperator, FilterOperator, JoinOperator, JoinKeyPair,
    AggregateOperator, UnionOperator,
)
from ir.pipeline_definition import support_case_pipeline_ir
from transform.join_config import TICKET_TYPE_TO_KB_CATEGORY
from versioning.branch import BranchStore, Branch
from versioning.diff import IRDiff, DiffEntry, DiffType, diff_pipeline_irs
from versioning.proposal import VersionProposal, ProposalStatus, ProposalStore
from versioning.rollback import VersionSnapshot, VersionHistory


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        raise AssertionError(f"{name} failed: {detail}")


print("Phase 8 smoke tests — Versioning / Proposal & Diff System")
print("=" * 60)

# ---------------------------------------------------------------------------
# Test 1: Editing a branch doesn't affect Main (FR-VER-01)
# ---------------------------------------------------------------------------
print("\nTest 1: Editing a branch doesn't affect Main (FR-VER-01)")

store = BranchStore(main_ir=support_case_pipeline_ir)

# Snapshot Main before any branch operations
main_before = store.get_main()
main_json_before = main_before.to_json()
main_step_count_before = main_before.step_count()

# Create a branch
branch = store.create_branch("feature/update-mapping")
test("Branch created", branch.name == "feature/update-mapping")
test("Branch has same step count as Main", branch.ir.step_count() == main_step_count_before)

# Edit the branch: add a new Filter operator
edited_ir = branch.ir.model_copy(deep=True)
edited_ir.add_step(FilterOperator(
    op="Filter",
    input="all_cases",
    condition="status == 'Open'",
    output="open_cases_only",
    source="manual",
))
store.update_branch("feature/update-mapping", edited_ir)

# Verify the branch was updated
updated_branch = store.get_branch("feature/update-mapping")
test("Branch now has one more step", updated_branch.ir.step_count() == main_step_count_before + 1)

# THE critical assertion: Main is unchanged
main_after = store.get_main()
main_json_after = main_after.to_json()
test("Main JSON unchanged after branch edit", main_json_before == main_json_after,
     "Main was mutated by a branch edit!")
test("Main step count unchanged", main_after.step_count() == main_step_count_before)

print(f"   Main: {main_step_count_before} steps, Branch: {updated_branch.ir.step_count()} steps")

# ---------------------------------------------------------------------------
# Test 2: Diff between two real IRs correctly identifies changes (FR-VER-02)
# Uses the support-case pipeline with a modified ticket_type -> category mapping
# ---------------------------------------------------------------------------
print("\nTest 2: Diff identifies added/modified/removed operators (FR-VER-02)")

# Create a modified version of the pipeline where the ticket_type -> category
# mapping changed for one entry: "Product inquiry" changes from "ORDER" to "PRODUCT"
modified_mapping = dict(TICKET_TYPE_TO_KB_CATEGORY)
modified_mapping["Product inquiry"] = "PRODUCT"  # was "ORDER"

# Build a modified pipeline IR with this changed mapping
modified_ir = support_case_pipeline_ir.model_copy(deep=True)
# Find the Join operator and replace its mapping
for i, step in enumerate(modified_ir.steps):
    if isinstance(step, JoinOperator):
        modified_ir.steps[i] = JoinOperator(
            op="Join",
            left=step.left,
            right=step.right,
            on=[JoinKeyPair(
                left_key="ticket_type",
                right_key="category",
                mapping=modified_mapping,
            )],
            type=step.type,
            one_to_many=step.one_to_many,
            output=step.output,
            source=step.source,
            confidence=step.confidence,
            review_status=step.review_status,
        )
        break

# Now also add a new Filter operator and remove the Union operator to test all three diff types
modified_ir.add_step(FilterOperator(
    op="Filter",
    input="all_cases",
    condition="priority == 'High'",
    output="high_priority_cases",
    source="manual",
))

# Remove the Union operator (the "all_cases" step) — construct a new steps list
# that excludes it, and adjust the Join to use tickets_normalized directly
modified_steps_no_union = []
for step in modified_ir.steps:
    if not isinstance(step, UnionOperator):
        modified_steps_no_union.append(step)
modified_ir.steps = modified_steps_no_union

# Compute the diff
diff = diff_pipeline_irs(support_case_pipeline_ir, modified_ir)

test("Diff is not empty", not diff.is_empty)
test("Diff has modified entries", diff.modified_count >= 1,
     f"got {diff.modified_count} modified")
test("Diff has added entries", diff.added_count >= 1,
     f"got {diff.added_count} added")
test("Diff has removed entries", diff.removed_count >= 1,
     f"got {diff.removed_count} removed")

# Find the modified Join operator (output="joined")
join_mods = [e for e in diff.entries if e.name == "joined" and e.diff_type == DiffType.MODIFIED]
test("Join operator 'joined' is modified", len(join_mods) == 1, f"got {len(join_mods)}")
if join_mods:
    detail = join_mods[0].change_detail
    test("Change detail mentions mapping change", "join mapping changed" in detail,
         f"detail: {detail}")
    test("Change detail mentions Product inquiry", "Product inquiry" in detail,
         f"detail: {detail}")
    test("Change detail mentions ORDER -> PRODUCT", "ORDER" in detail and "PRODUCT" in detail,
         f"detail: {detail}")
    print(f"   Join change detail: {detail}")

# Find the added Filter operator
filter_adds = [e for e in diff.entries if e.name == "high_priority_cases" and e.diff_type == DiffType.ADDED]
test("Filter operator 'high_priority_cases' is added", len(filter_adds) == 1)

# Find the removed Union operator
union_removals = [e for e in diff.entries if e.name == "all_cases" and e.diff_type == DiffType.REMOVED]
test("Union operator 'all_cases' is removed", len(union_removals) == 1)

print(f"   Diff: {diff.added_count} added, {diff.removed_count} removed, {diff.modified_count} modified")

# Also test the human-readable output
hr = diff.human_readable()
test("Human-readable output is a string", isinstance(hr, str))
test("Human-readable mentions pipeline names", support_case_pipeline_ir.name in hr)

# Test that identical IRs produce an empty diff
identical_diff = diff_pipeline_irs(support_case_pipeline_ir, support_case_pipeline_ir)
test("Identical IRs produce empty diff", identical_diff.is_empty)

# ---------------------------------------------------------------------------
# Test 3: Merge is blocked without review (FR-VER-03)
# ---------------------------------------------------------------------------
print("\nTest 3: Merge is blocked without review (FR-VER-03)")

store3 = BranchStore(main_ir=support_case_pipeline_ir)
proposals3 = ProposalStore(branch_store=store3)

store3.create_branch("feature/test-merge")
proposal = proposals3.create_proposal(proposer="alice", branch_name="feature/test-merge")
test("Proposal created with status 'open'", proposal.status == ProposalStatus.OPEN)
test("Proposal has a frozen diff", proposal.diff is not None)

# Attempt to merge without review — should fail
try:
    proposals3.merge_proposal(proposal_id=proposal.id)
    test("Merge without review raises ValueError", False, "merge succeeded without review!")
except ValueError as e:
    test("Merge without review raises ValueError", True)
    test("Error message mentions 'approved'", "approved" in str(e).lower())

# ---------------------------------------------------------------------------
# Test 4: Merge is blocked if reviewer == proposer (FR-VER-03)
# ---------------------------------------------------------------------------
print("\nTest 4: Merge is blocked if reviewer == proposer (FR-VER-03)")

store4 = BranchStore(main_ir=support_case_pipeline_ir)
proposals4 = ProposalStore(branch_store=store4)

store4.create_branch("feature/self-review")
proposal4 = proposals4.create_proposal(proposer="alice", branch_name="feature/self-review")

# Attempt to review with the same identity as proposer — should fail
try:
    proposals4.review_proposal(proposal_id=proposal4.id, reviewer="alice", action="approve")
    test("Self-review raises ValueError", False, "self-review succeeded!")
except ValueError as e:
    test("Self-review raises ValueError", True)
    test("Error message mentions 'second-party'", "second-party" in str(e).lower())

# Verify the proposal is still open (review was rejected)
proposal4_after = proposals4.get_proposal(proposal4.id)
test("Proposal still open after failed self-review", proposal4_after.status == ProposalStatus.OPEN)

# ---------------------------------------------------------------------------
# Test 5: Merge succeeds with valid distinct-reviewer approval (FR-VER-03)
# ---------------------------------------------------------------------------
print("\nTest 5: Merge succeeds with valid distinct-reviewer approval (FR-VER-03)")

store5 = BranchStore(main_ir=support_case_pipeline_ir)
proposals5 = ProposalStore(branch_store=store5)
history5 = VersionHistory(branch_store=store5)
history5.snapshot_initial()

# Create a branch with a real change
store5.create_branch("feature/add-filter")
branch5 = store5.get_branch("feature/add-filter")
edited_ir5 = branch5.ir.model_copy(deep=True)
edited_ir5.add_step(FilterOperator(
    op="Filter",
    input="all_cases",
    condition="status == 'Open'",
    output="open_only",
    source="manual",
))
store5.update_branch("feature/add-filter", edited_ir5)

# Verify Main is still unchanged
test("Main unchanged before merge", store5.get_main().step_count() == support_case_pipeline_ir.step_count())

# Create proposal
proposal5 = proposals5.create_proposal(proposer="alice", branch_name="feature/add-filter")
test("Proposal created", proposal5.status == ProposalStatus.OPEN)

# Review with a DISTINCT reviewer
review_result = proposals5.review_proposal(
    proposal_id=proposal5.id,
    reviewer="bob",  # different from proposer "alice"
    action="approve",
    reason="Filter looks correct",
)
test("Review approved", review_result.status == ProposalStatus.APPROVED)
test("Reviewer recorded as bob", review_result.reviewer == "bob")
test("Review reason recorded", review_result.review_reason == "Filter looks correct")

# Snapshot Main before merge
main_before_merge = store5.get_main().to_json()

# Now merge — should succeed
merge_result = proposals5.merge_proposal(proposal_id=proposal5.id)
test("Merge succeeded", merge_result.status == ProposalStatus.MERGED)
test("Merge recorded timestamp", merge_result.merged_at is not None)
test("Merge note mentions pipeline logic", "pipeline logic" in merge_result.merge_note)

# Verify Main was actually updated
main_after_merge = store5.get_main()
test("Main now has the new step", main_after_merge.step_count() == support_case_pipeline_ir.step_count() + 1)
test("Main JSON changed after merge", main_before_merge != main_after_merge.to_json())

# Verify the branch is marked as merged
branch5_after = store5.get_branch("feature/add-filter")
test("Branch marked as merged", branch5_after.is_merged)

# Cannot create a new proposal for a merged branch
try:
    proposals5.create_proposal(proposer="charlie", branch_name="feature/add-filter")
    test("Cannot propose already-merged branch", False, "proposal created for merged branch!")
except ValueError:
    test("Cannot propose already-merged branch", True)

print(f"   Proposal: {merge_result.summary()}")

# ---------------------------------------------------------------------------
# Test 6: Rollback restores a prior version correctly (FR-VER-04)
# ---------------------------------------------------------------------------
print("\nTest 6: Rollback restores a prior version correctly (FR-VER-04)")

store6 = BranchStore(main_ir=support_case_pipeline_ir)
proposals6 = ProposalStore(branch_store=store6)
history6 = VersionHistory(branch_store=store6)

# Capture initial version
v1 = history6.snapshot_initial()
test("Initial snapshot is version 1", v1.version == 1)
test("Initial snapshot reason is 'initial'", v1.reason == "initial")

original_step_count = store6.get_main().step_count()

# Create a branch, make a change, merge it
store6.create_branch("feature/add-step")
branch6 = store6.get_branch("feature/add-step")
edited_ir6 = branch6.ir.model_copy(deep=True)
edited_ir6.add_step(FilterOperator(
    op="Filter",
    input="all_cases",
    condition="status == 'Closed'",
    output="closed_only",
    source="manual",
))
store6.update_branch("feature/add-step", edited_ir6)

proposal6 = proposals6.create_proposal(proposer="alice", branch_name="feature/add-step")
proposals6.review_proposal(proposal_id=proposal6.id, reviewer="bob", action="approve")

# Snapshot pre-merge
history6.snapshot_pre_merge(previous_main_ir=store6.get_main(), branch_name="feature/add-step")
proposals6.merge_proposal(proposal_id=proposal6.id)
# Snapshot post-merge
history6.snapshot_post_merge(branch_name="feature/add-step")

test("Main has new step after merge", store6.get_main().step_count() == original_step_count + 1)

# List versions
versions = history6.list_versions()
test("History has multiple versions", len(versions) >= 3)

# Rollback to version 1 (the initial version)
rollback_result = history6.rollback_to(version=1)
test("Rollback returned a snapshot", isinstance(rollback_result, VersionSnapshot))
test("Rollback reason is 'rollback'", rollback_result.reason == "rollback")
test("Rollback recorded rolled_back_from", rollback_result.rolled_back_from == 1)

# Verify Main was restored to the original
test("Main restored to original step count", store6.get_main().step_count() == original_step_count)
test("Main JSON matches original after rollback",
     store6.get_main().to_json() == support_case_pipeline_ir.to_json())

# Cannot rollback to the current version (no-op)
try:
    history6.rollback_to(version=rollback_result.version)
    test("Rollback to current version raises ValueError", False, "no-op rollback succeeded!")
except ValueError:
    test("Rollback to current version raises ValueError", True)

# Cannot rollback to a nonexistent version
try:
    history6.rollback_to(version=999)
    test("Rollback to nonexistent version raises KeyError", False)
except KeyError:
    test("Rollback to nonexistent version raises KeyError", True)

print(f"   Versions: {[s.summary() for s in history6.list_versions()]}")

# ---------------------------------------------------------------------------
# Test 7: Merge does NOT deploy — merge and deploy are distinct actions
# ---------------------------------------------------------------------------
print("\nTest 7: Merge does NOT deploy (hard requirement)")

# Verify that merge_proposal's docstring explicitly states it doesn't deploy
import inspect
merge_doc = inspect.getdoc(ProposalStore.merge_proposal)
test("merge_proposal docstring says it does NOT deploy", "does NOT deploy" in merge_doc)
test("merge_proposal docstring mentions deploy_gate", "deploy_gate" in merge_doc)

# Verify that the merge note in a completed proposal mentions this
store7 = BranchStore(main_ir=support_case_pipeline_ir)
proposals7 = ProposalStore(branch_store=store7)
store7.create_branch("feature/x")
proposals7.create_proposal(proposer="alice", branch_name="feature/x")
proposals7.review_proposal(proposal_id=1, reviewer="bob", action="approve")
merged = proposals7.merge_proposal(proposal_id=1)
test("Merge note mentions 'pipeline logic'", "pipeline logic" in merged.merge_note)
test("Merge note mentions 'deploy'", "deploy" in merged.merge_note)

# ---------------------------------------------------------------------------
# Test 8: get_branch raises a clear error for nonexistent branches (no silent None)
# ---------------------------------------------------------------------------
print("\nTest 8: No silent None returns — clear errors for missing entities")

store8 = BranchStore(main_ir=support_case_pipeline_ir)

# get_branch on nonexistent branch
try:
    store8.get_branch("nonexistent")
    test("get_branch raises KeyError for nonexistent", False)
except KeyError as e:
    test("get_branch raises KeyError for nonexistent", True)
    test("Error message lists available branches", "Available" in str(e))

# get_proposal on nonexistent ID
proposals8 = ProposalStore(branch_store=store8)
try:
    proposals8.get_proposal(999)
    test("get_proposal raises KeyError for nonexistent", False)
except KeyError as e:
    test("get_proposal raises KeyError for nonexistent", True)

# diff_pipeline_irs with None inputs
try:
    diff_pipeline_irs(None, support_case_pipeline_ir)
    test("diff with None old raises ValueError", False)
except ValueError:
    test("diff with None old raises ValueError", True)

try:
    diff_pipeline_irs(support_case_pipeline_ir, None)
    test("diff with None new raises ValueError", False)
except ValueError:
    test("diff with None new raises ValueError", True)

# BranchStore with None main_ir
try:
    BranchStore(main_ir=None)
    test("BranchStore with None raises ValueError", False)
except ValueError:
    test("BranchStore with None raises ValueError", True)

# get_version on nonexistent version
history8 = VersionHistory(branch_store=store8)
history8.snapshot_initial()
try:
    history8.get_version(999)
    test("get_version raises KeyError for nonexistent", False)
except KeyError:
    test("get_version raises KeyError for nonexistent", True)

print("\n" + "=" * 60)
print("All Phase 8 smoke tests passed.")