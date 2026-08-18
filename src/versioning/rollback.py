"""
Rollback — FR-VER-04 (Rollback).

Maintains a history of Main pipeline versions and allows reverting Main
to any previously deployed version.

Design:
- VersionHistory holds a list of VersionSnapshot objects, each capturing
  a Main PipelineIR at a point in time.
- snapshot() is called after every successful merge (by the MCP merge tool)
  and after every rollback (so rollbacks themselves are auditable).
- rollback_to() replaces Main with a historical version's IR and records
  the rollback as a new snapshot.
- This is Should-priority (FR-VER-04) — implemented simply and correctly
  without over-engineering. No time-based expiry or retention policy.

Relationship to deploy:
- Rollback reverts the PIPELINE LOGIC (the PipelineIR that defines what
  the pipeline does). It does NOT deploy output. After a rollback, the user
  must still run the pipeline and deploy through deploy_gate.py if they
  want to produce output from the reverted logic.
- This keeps rollback and deploy as distinct actions, consistent with the
  hard requirement that deploy_gate.py is never bypassed.
"""

from datetime import datetime, timezone
from pydantic import BaseModel, Field
from ir.pipeline_ir import PipelineIR
from .branch import BranchStore


class VersionSnapshot(BaseModel):
    """
    A snapshot of Main at a point in time.

    Attributes:
        version: sequential version number (1, 2, 3, ...)
        ir: the PipelineIR at this version
        captured_at: when the snapshot was taken
        reason: why this snapshot was taken ("initial", "pre_merge", "post_merge", "rollback")
        branch_name: the branch that was merged (if reason is merge-related)
        rolled_back_from: the version this rollback reverted from (if reason is "rollback")
    """
    version: int
    ir: PipelineIR
    captured_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str = ""
    branch_name: str = ""
    rolled_back_from: int | None = None

    def summary(self) -> dict:
        """Human-readable summary for MCP output."""
        return {
            "version": self.version,
            "pipeline_name": self.ir.name,
            "step_count": self.ir.step_count(),
            "captured_at": self.captured_at,
            "reason": self.reason,
            "branch_name": self.branch_name,
            "rolled_back_from": self.rolled_back_from,
        }


class VersionHistory:
    """
    Manages version history for a BranchStore's Main pipeline.

    Usage:
        history = VersionHistory(branch_store)
        history.snapshot_initial()  # capture the starting Main
        # ... merge happens ...
        history.snapshot_pre_merge(previous_main_ir, branch_name="feature/x")
        history.snapshot_post_merge(branch_name="feature/x")
        # ... later, rollback ...
        history.rollback_to(version=1)
    """

    def __init__(self, branch_store: BranchStore):
        if branch_store is None:
            raise ValueError("branch_store must not be None — VersionHistory needs a BranchStore")
        self._branch_store = branch_store
        self._snapshots: list[VersionSnapshot] = []
        self._next_version: int = 1

    def snapshot_initial(self) -> VersionSnapshot:
        """Capture the initial Main version. Called once at startup."""
        snapshot = VersionSnapshot(
            version=self._next_version,
            ir=self._branch_store.get_main().model_copy(deep=True),
            reason="initial",
        )
        self._snapshots.append(snapshot)
        self._next_version += 1
        return snapshot

    def snapshot_pre_merge(self, previous_main_ir: PipelineIR, branch_name: str = "") -> VersionSnapshot:
        """
        Capture the Main version BEFORE a merge replaces it.
        The caller passes the previous Main IR (returned by branch_store.set_main()).
        """
        if previous_main_ir is None:
            raise ValueError("previous_main_ir must not be None — cannot snapshot nothing")
        snapshot = VersionSnapshot(
            version=self._next_version,
            ir=previous_main_ir.model_copy(deep=True),
            reason="pre_merge",
            branch_name=branch_name,
        )
        self._snapshots.append(snapshot)
        self._next_version += 1
        return snapshot

    def snapshot_post_merge(self, branch_name: str = "") -> VersionSnapshot:
        """
        Capture the Main version AFTER a merge.
        Reads the current Main from the BranchStore.
        """
        snapshot = VersionSnapshot(
            version=self._next_version,
            ir=self._branch_store.get_main().model_copy(deep=True),
            reason="post_merge",
            branch_name=branch_name,
        )
        self._snapshots.append(snapshot)
        self._next_version += 1
        return snapshot

    def list_versions(self) -> list[VersionSnapshot]:
        """Return all version snapshots, oldest first."""
        return list(self._snapshots)

    def get_version(self, version: int) -> VersionSnapshot:
        """
        Retrieve a specific version snapshot.

        Raises KeyError if not found — never returns None.
        """
        for snap in self._snapshots:
            if snap.version == version:
                return snap
        raise KeyError(
            f"Version {version} does not exist. "
            f"Available versions: {[s.version for s in self._snapshots]}"
        )

    def get_latest_version(self) -> VersionSnapshot:
        """
        Return the most recent version snapshot.

        Raises RuntimeError if no snapshots exist.
        """
        if not self._snapshots:
            raise RuntimeError("No version snapshots exist — call snapshot_initial() first.")
        return self._snapshots[-1]

    def rollback_to(self, version: int) -> VersionSnapshot:
        """
        Revert Main to a previously deployed version.

        This replaces Main's PipelineIR with the historical version's IR.
        The rollback itself is recorded as a new snapshot for auditability.

        This is a PIPELINE LOGIC change only — it does NOT deploy output.
        After rollback, the user must still run the pipeline and deploy
        through deploy_gate.py if they want output from the reverted logic.

        Raises:
            KeyError: if the target version doesn't exist
            ValueError: if the target version is the current version (no-op)
        """
        target = self.get_version(version)  # raises KeyError if not found

        current_main = self._branch_store.get_main()
        current_ir_json = current_main.to_json()
        target_ir_json = target.ir.to_json()

        if current_ir_json == target_ir_json:
            raise ValueError(
                f"Version {version} is identical to the current Main — "
                f"rollback is a no-op. Use a different version."
            )

        # Replace Main with the historical version's IR
        self._branch_store.set_main(target.ir.model_copy(deep=True))

        # Record the rollback as a new snapshot
        snapshot = VersionSnapshot(
            version=self._next_version,
            ir=self._branch_store.get_main().model_copy(deep=True),
            reason="rollback",
            rolled_back_from=version,
        )
        self._snapshots.append(snapshot)
        self._next_version += 1
        return snapshot

    def summary(self) -> dict:
        """Return a summary of the version history."""
        return {
            "total_versions": len(self._snapshots),
            "latest_version": self._snapshots[-1].version if self._snapshots else 0,
            "versions": [s.summary() for s in self._snapshots],
        }