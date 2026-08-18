"""
Propose/review/merge workflow — FR-VER-03.

A VersionProposal is the explicit proposal object that must exist before
a branch can be merged into Main. The workflow is:

  1. create_proposal(proposer, branch_name)  -> proposal is "open"
  2. review_proposal(reviewer, action)       -> reviewer approves or rejects
     - reviewer MUST differ from proposer (second-party review)
     - proposal must be "open" (cannot review a merged/rejected proposal)
  3. merge_proposal()                        -> replaces Main with the branch IR
     - proposal must be "approved"
     - merge calls branch_store.set_main() — this is a PIPELINE LOGIC change,
       NOT a deploy. Deploying output still requires deploy_gate.py with
       is_safe=True AND approved=True. Merge and deploy are distinct actions.

Design:
- ProposalStore holds all proposals for a BranchStore.
- Each proposal has a unique integer ID for easy reference.
- The diff is computed at proposal creation time and frozen into the proposal
  so the reviewer sees exactly what was proposed (not a re-computation at
  review time that might pick up new edits).
- Proposals are retained after merge/reject for auditability.
"""

from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
from ir.pipeline_ir import PipelineIR
from .branch import BranchStore
from .diff import IRDiff, diff_pipeline_irs


class ProposalStatus(str, Enum):
    """The lifecycle states of a proposal."""
    OPEN = "open"           # created, awaiting review
    APPROVED = "approved"   # reviewed and approved by a second party
    REJECTED = "rejected"   # reviewed and rejected
    MERGED = "merged"       # approved and merged into Main
    SUPERSEDED = "superseded"  # replaced by a newer proposal for the same branch


class ReviewRecord(BaseModel):
    """A record of a review action on a proposal."""
    reviewer: str
    action: str               # "approve" or "reject"
    reviewed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str = ""


class VersionProposal(BaseModel):
    """
    A proposal to merge a branch into Main.

    Attributes:
        id: unique integer ID
        proposer: identity of who created the proposal
        branch_name: the branch being proposed for merge
        reviewer: identity of who reviewed (None until reviewed)
        status: current lifecycle state
        diff: the frozen IRDiff at proposal creation time
        created_at: when the proposal was created
        reviewed_at: when the review happened (None until reviewed)
        merged_at: when the merge happened (None until merged)
        review_reason: optional reason given during review
        rejection_reason: optional reason if rejected
        merge_note: note about what the merge did (for audit)
    """
    id: int
    proposer: str
    branch_name: str
    reviewer: str | None = None
    status: ProposalStatus = ProposalStatus.OPEN
    diff: IRDiff
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reviewed_at: str | None = None
    merged_at: str | None = None
    review_reason: str = ""
    rejection_reason: str = ""
    merge_note: str = ""

    def summary(self) -> dict:
        """Human-readable summary for MCP output."""
        return {
            "id": self.id,
            "proposer": self.proposer,
            "branch_name": self.branch_name,
            "reviewer": self.reviewer,
            "status": self.status.value,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "merged_at": self.merged_at,
            "diff_summary": self.diff.summary(),
            "review_reason": self.review_reason,
            "rejection_reason": self.rejection_reason,
            "merge_note": self.merge_note,
        }


class ProposalStore:
    """
    Manages proposals for a BranchStore.

    Enforces the review-then-merge workflow:
    - create_proposal() creates an open proposal with a frozen diff
    - review_proposal() requires reviewer != proposer (second-party review)
    - merge_proposal() requires status == APPROVED
    - Merge replaces Main's PipelineIR via branch_store.set_main() — this is
      a PIPELINE LOGIC change only. It does NOT deploy output. Deploying
      output still requires deploy_gate.py (is_safe=True AND approved=True).
      Merge and deploy are distinct actions by design.
    """

    def __init__(self, branch_store: BranchStore):
        if branch_store is None:
            raise ValueError("branch_store must not be None — ProposalStore needs a BranchStore to manage")
        self._branch_store = branch_store
        self._proposals: dict[int, VersionProposal] = {}
        self._next_id: int = 1

    def create_proposal(self, proposer: str, branch_name: str) -> VersionProposal:
        """
        Create a new proposal to merge a branch into Main.

        Computes and freezes the diff at creation time so the reviewer
        sees exactly what was proposed.

        Raises:
            ValueError: if proposer is empty or branch is already merged.
            KeyError: if branch doesn't exist.
        """
        if not proposer or not proposer.strip():
            raise ValueError("proposer must not be empty — every proposal needs a named proposer")
        if not branch_name or not branch_name.strip():
            raise ValueError("branch_name must not be empty")

        branch = self._branch_store.get_branch(branch_name)  # raises KeyError if not found

        if branch.is_merged:
            raise ValueError(
                f"Branch '{branch_name}' has already been merged — "
                f"cannot create a new proposal for it."
            )

        # Check for existing open proposals for this branch and supersede them
        for existing in self._proposals.values():
            if existing.branch_name == branch_name and existing.status == ProposalStatus.OPEN:
                existing.status = ProposalStatus.SUPERSEDED

        # Compute and freeze the diff
        main_ir = self._branch_store.get_main()
        diff = diff_pipeline_irs(main_ir, branch.ir)

        proposal = VersionProposal(
            id=self._next_id,
            proposer=proposer,
            branch_name=branch_name,
            diff=diff,
        )
        self._proposals[self._next_id] = proposal
        self._next_id += 1
        return proposal

    def get_proposal(self, proposal_id: int) -> VersionProposal:
        """
        Retrieve a proposal by ID.

        Raises KeyError if not found — never returns None.
        """
        if proposal_id not in self._proposals:
            raise KeyError(
                f"Proposal {proposal_id} does not exist. "
                f"Available IDs: {list(self._proposals.keys())}"
            )
        return self._proposals[proposal_id]

    def list_proposals(self, status: ProposalStatus | None = None) -> list[VersionProposal]:
        """List all proposals, optionally filtered by status."""
        if status is None:
            return list(self._proposals.values())
        return [p for p in self._proposals.values() if p.status == status]

    def review_proposal(
        self,
        proposal_id: int,
        reviewer: str,
        action: str,
        reason: str = "",
    ) -> VersionProposal:
        """
        Review a proposal — approve or reject it.

        Enforces second-party review: reviewer MUST differ from proposer.

        Args:
            proposal_id: the proposal to review
            reviewer: identity of the reviewer (must differ from proposer)
            action: "approve" or "reject"
            reason: optional reason (required if rejecting)

        Raises:
            KeyError: if proposal doesn't exist
            ValueError: if reviewer == proposer, proposal not open, or invalid action
        """
        if not reviewer or not reviewer.strip():
            raise ValueError("reviewer must not be empty — every review needs a named reviewer")

        proposal = self.get_proposal(proposal_id)  # raises KeyError if not found

        if proposal.status != ProposalStatus.OPEN:
            raise ValueError(
                f"Proposal {proposal_id} is not open for review — "
                f"current status: {proposal.status.value}. "
                f"Only open proposals can be reviewed."
            )

        if reviewer == proposal.proposer:
            raise ValueError(
                f"Reviewer '{reviewer}' is the same as the proposer '{proposal.proposer}'. "
                f"Second-party review requires a distinct reviewer."
            )

        if action == "approve":
            proposal.status = ProposalStatus.APPROVED
            proposal.reviewer = reviewer
            proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
            proposal.review_reason = reason
        elif action == "reject":
            if not reason:
                raise ValueError("A reason must be provided when rejecting a proposal.")
            proposal.status = ProposalStatus.REJECTED
            proposal.reviewer = reviewer
            proposal.reviewed_at = datetime.now(timezone.utc).isoformat()
            proposal.rejection_reason = reason
        else:
            raise ValueError(f"Invalid action '{action}'. Must be 'approve' or 'reject'.")

        return proposal

    def merge_proposal(self, proposal_id: int) -> VersionProposal:
        """
        Merge an approved proposal's branch into Main.

        This replaces Main's PipelineIR with the branch's IR via
        branch_store.set_main(). This is a PIPELINE LOGIC change only.

        IMPORTANT: This does NOT deploy output. Deploying output still
        requires deploy_gate.py (is_safe=True AND approved=True).
        Merge and deploy are distinct actions by design — merging a pipeline
        change into Main makes it the current pipeline definition, but
        producing and deploying output data is a separate explicit step.

        Raises:
            KeyError: if proposal doesn't exist
            ValueError: if proposal is not approved
        """
        proposal = self.get_proposal(proposal_id)  # raises KeyError if not found

        if proposal.status != ProposalStatus.APPROVED:
            raise ValueError(
                f"Proposal {proposal_id} cannot be merged — "
                f"current status: {proposal.status.value}. "
                f"Only approved proposals can be merged. "
                f"Review with a distinct reviewer first."
            )

        branch = self._branch_store.get_branch(proposal.branch_name)

        # Replace Main with the branch's IR
        # set_main returns the previous Main IR — we don't need it here
        # (version history is managed by rollback.py's VersionHistory)
        self._branch_store.set_main(branch.ir)
        self._branch_store.mark_branch_merged(proposal.branch_name)

        proposal.status = ProposalStatus.MERGED
        proposal.merged_at = datetime.now(timezone.utc).isoformat()
        proposal.merge_note = (
            f"Merged branch '{proposal.branch_name}' into Main. "
            f"This is a pipeline logic change only — output deployment "
            f"requires a separate deploy action (deploy_gate.py)."
        )

        return proposal

    def get_open_proposals(self) -> list[VersionProposal]:
        """Return all proposals with status OPEN."""
        return self.list_proposals(ProposalStatus.OPEN)

    def get_pending_review(self) -> list[VersionProposal]:
        """Return all proposals awaiting review (same as open)."""
        return self.get_open_proposals()