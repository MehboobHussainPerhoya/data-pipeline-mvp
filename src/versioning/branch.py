"""
Branch management — FR-VER-01 (Branch-based editing).

A Branch is a named, isolated copy of a PipelineIR that can be edited
without affecting the live ("Main") version. This is NOT git-branch
semantics — it is the simplest correct implementation: a named copy
of an IR that can be independently modified.

Design:
- BranchStore holds the "Main" PipelineIR and a dict of named branches.
- Creating a branch deep-copies the source IR so edits are fully isolated.
- get_branch() raises a clear error if the branch doesn't exist — it never
  silently returns None (per the standing rule from last phase's bug).
- set_main() is the low-level primitive that replaces Main — it is called
  by the proposal/merge workflow (proposal.py) after review is enforced,
  and by rollback.py when reverting. This module does NOT enforce review;
  that is proposal.py's responsibility.
"""

from datetime import datetime, timezone
from pydantic import BaseModel, Field
from ir.pipeline_ir import PipelineIR


class Branch(BaseModel):
    """
    A named, isolated copy of a PipelineIR.

    Attributes:
        name: the branch name (e.g., "feature/update-mapping")
        ir: the PipelineIR copy — edits to this do not affect Main
        created_at: when the branch was created
        created_from: the branch name this was copied from ("Main" or another branch)
        parent_main_summary: a snapshot of Main's name/step_count at branch creation
                             for traceability — does not hold a reference to the old Main IR
        is_merged: True if this branch has been merged into Main
    """
    name: str
    ir: PipelineIR
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_from: str = "Main"
    parent_main_summary: dict = {}
    is_merged: bool = False

    def summary(self) -> dict:
        """Return a human-readable summary for MCP tool output."""
        return {
            "name": self.name,
            "pipeline_name": self.ir.name,
            "step_count": self.ir.step_count(),
            "created_at": self.created_at,
            "created_from": self.created_from,
            "is_merged": self.is_merged,
            "parent_main_summary": self.parent_main_summary,
        }


class BranchStore:
    """
    Manages the Main pipeline version and named branches.

    Usage:
        store = BranchStore(main_ir=support_case_pipeline_ir)
        store.create_branch("feature/update-mapping")
        branch = store.get_branch("feature/update-mapping")
        # edit branch.ir without affecting Main
        store.get_main()  # still the original
    """

    def __init__(self, main_ir: PipelineIR):
        if main_ir is None:
            raise ValueError("main_ir must not be None — BranchStore requires a live Main pipeline")
        # Deep copy so the store owns its own copy of Main
        self._main_ir: PipelineIR = main_ir.model_copy(deep=True)
        self._branches: dict[str, Branch] = {}

    def get_main(self) -> PipelineIR:
        """Return the current Main PipelineIR."""
        return self._main_ir

    def create_branch(self, name: str, from_branch: str = "Main") -> Branch:
        """
        Create a new branch as an isolated copy of the given source branch
        (or Main by default). The branch gets its own deep copy of the IR
        so edits do not affect the source.

        Raises ValueError if the branch name already exists.
        Raises KeyError if from_branch doesn't exist.
        """
        if name == "Main":
            raise ValueError("Cannot create a branch named 'Main' — that name is reserved.")
        if name in self._branches:
            raise ValueError(f"Branch '{name}' already exists. Use get_branch() or update_branch().")

        # Get the source IR to copy from
        if from_branch == "Main":
            source_ir = self._main_ir
        else:
            if from_branch not in self._branches:
                raise KeyError(
                    f"Source branch '{from_branch}' does not exist. "
                    f"Available: {['Main'] + list(self._branches.keys())}"
                )
            source_ir = self._branches[from_branch].ir

        # Deep copy so the branch is fully isolated
        branch_ir = source_ir.model_copy(deep=True)

        branch = Branch(
            name=name,
            ir=branch_ir,
            created_from=from_branch,
            parent_main_summary={
                "pipeline_name": self._main_ir.name,
                "step_count": self._main_ir.step_count(),
            },
        )
        self._branches[name] = branch
        return branch

    def get_branch(self, name: str) -> Branch:
        """
        Retrieve a branch by name.

        Raises KeyError if the branch doesn't exist — never returns None.
        (Per standing rule: a silent None return hides exactly the kind of
        bug we spent an extra round catching last phase.)
        """
        if name not in self._branches:
            raise KeyError(
                f"Branch '{name}' does not exist. "
                f"Available branches: {list(self._branches.keys())}"
            )
        return self._branches[name]

    def list_branches(self) -> list[Branch]:
        """Return all branches (not including Main)."""
        return list(self._branches.values())

    def update_branch(self, name: str, ir: PipelineIR) -> Branch:
        """
        Replace a branch's PipelineIR with a new version.
        This is how edits to a branch are saved.

        Raises KeyError if the branch doesn't exist.
        """
        if name not in self._branches:
            raise KeyError(
                f"Branch '{name}' does not exist. "
                f"Available branches: {list(self._branches.keys())}"
            )
        if self._branches[name].is_merged:
            raise ValueError(f"Branch '{name}' has already been merged and cannot be edited.")
        self._branches[name].ir = ir.model_copy(deep=True)
        return self._branches[name]

    def set_main(self, ir: PipelineIR) -> PipelineIR:
        """
        Replace the Main PipelineIR. Returns the PREVIOUS Main IR so the
        caller (proposal.py merge or rollback.py revert) can snapshot it
        into version history.

        This is the low-level primitive — it does NOT enforce review.
        The proposal/merge workflow in proposal.py is responsible for
        enforcing the review-then-merge sequence.
        """
        previous_main = self._main_ir
        self._main_ir = ir.model_copy(deep=True)
        return previous_main

    def mark_branch_merged(self, name: str) -> Branch:
        """
        Mark a branch as merged into Main. The branch is retained (not
        deleted) for auditability — its is_merged flag is set to True.

        Raises KeyError if the branch doesn't exist.
        """
        if name not in self._branches:
            raise KeyError(
                f"Branch '{name}' does not exist. "
                f"Available branches: {list(self._branches.keys())}"
            )
        self._branches[name].is_merged = True
        return self._branches[name]

    def delete_branch(self, name: str) -> None:
        """
        Delete a branch. Raises KeyError if it doesn't exist.
        Cannot delete a merged branch (retain for audit).
        """
        if name not in self._branches:
            raise KeyError(
                f"Branch '{name}' does not exist. "
                f"Available branches: {list(self._branches.keys())}"
            )
        if self._branches[name].is_merged:
            raise ValueError(
                f"Cannot delete branch '{name}' — it has been merged into Main "
                f"and must be retained for auditability."
            )
        del self._branches[name]