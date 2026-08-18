"""
Versioning package — Versioning / Proposal & Diff System (FSD 4.13).

Provides branch-based editing, operator-level diffing, propose/review/merge
workflow, and rollback for pipeline logic changes.

Key exports:
- Branch, BranchStore: branch management (FR-VER-01)
- IRDiff, DiffEntry, diff_pipeline_irs: IR diffing (FR-VER-02)
- VersionProposal, ProposalStore: propose/review/merge (FR-VER-03)
- VersionSnapshot, VersionHistory: rollback (FR-VER-04)
"""

from .branch import Branch, BranchStore
from .diff import IRDiff, DiffEntry, DiffType, diff_pipeline_irs
from .proposal import VersionProposal, ProposalStatus, ProposalStore
from .rollback import VersionSnapshot, VersionHistory

__all__ = [
    "Branch",
    "BranchStore",
    "IRDiff",
    "DiffEntry",
    "DiffType",
    "diff_pipeline_irs",
    "VersionProposal",
    "ProposalStatus",
    "ProposalStore",
    "VersionSnapshot",
    "VersionHistory",
]