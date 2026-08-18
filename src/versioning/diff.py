"""
IR diffing — FR-VER-02 (Change diffing).

Produces an operator-level diff between two PipelineIR objects: which
operators were added, removed, or modified. This is NOT a text diff of
serialized JSON — it is a structural comparison that a human reviewer
can read and understand.

Design:
- Operators are matched by their `output` name (the dataset they produce).
  This is the stable identity of an operator within a pipeline — if an
  operator's output name changes, it's treated as remove+add, not modify.
- "Modified" means the operator type changed OR any parameter changed.
  We compare by serializing both operators to dicts and comparing — this
  catches any field-level change (join keys, mapping, condition, etc.).
- Added/removed input sources are also reported.
- The diff is deterministic and human-readable.
"""

from enum import Enum
from pydantic import BaseModel
from ir.pipeline_ir import PipelineIR, InputSource
from ir.operators import Operator


class DiffType(str, Enum):
    """The kind of change an entry represents."""
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class DiffEntry(BaseModel):
    """
    One entry in an IR diff — a single added/removed/modified operator
    or input source.

    Attributes:
        diff_type: added, removed, or modified
        element_type: "operator" or "input_source"
        name: the output name (for operators) or source name (for inputs)
        operator_type: the op type ("Join", "Map", etc.) — None for input sources
        old_value: dict snapshot of the old state (None for added)
        new_value: dict snapshot of the new state (None for removed)
        change_detail: human-readable description of what specifically changed
    """
    diff_type: DiffType
    element_type: str          # "operator" or "input_source"
    name: str
    operator_type: str | None = None
    old_value: dict | None = None
    new_value: dict | None = None
    change_detail: str = ""

    def summary(self) -> dict:
        """Human-readable summary for MCP output."""
        return {
            "diff_type": self.diff_type.value,
            "element_type": self.element_type,
            "name": self.name,
            "operator_type": self.operator_type,
            "change_detail": self.change_detail,
        }


class IRDiff(BaseModel):
    """
    The complete diff between two PipelineIR objects.

    Attributes:
        old_name: name of the old pipeline (Main)
        new_name: name of the new pipeline (branch)
        entries: list of DiffEntry objects
        added_count / removed_count / modified_count: convenience counts
        is_empty: True if the two IRs are identical
    """
    old_name: str
    new_name: str
    entries: list[DiffEntry] = []

    @property
    def added_count(self) -> int:
        return sum(1 for e in self.entries if e.diff_type == DiffType.ADDED)

    @property
    def removed_count(self) -> int:
        return sum(1 for e in self.entries if e.diff_type == DiffType.REMOVED)

    @property
    def modified_count(self) -> int:
        return sum(1 for e in self.entries if e.diff_type == DiffType.MODIFIED)

    @property
    def is_empty(self) -> bool:
        return len(self.entries) == 0

    def summary(self) -> dict:
        """Human-readable summary for MCP output."""
        return {
            "old_name": self.old_name,
            "new_name": self.new_name,
            "added": self.added_count,
            "removed": self.removed_count,
            "modified": self.modified_count,
            "total_changes": len(self.entries),
            "is_empty": self.is_empty,
            "entries": [e.summary() for e in self.entries],
        }

    def human_readable(self) -> str:
        """Return a multi-line human-readable diff report."""
        if self.is_empty:
            return f"No differences between '{self.old_name}' and '{self.new_name}'."

        lines = [
            f"Diff: '{self.old_name}' -> '{self.new_name}'",
            f"  Added: {self.added_count}  Removed: {self.removed_count}  Modified: {self.modified_count}",
            "",
        ]
        for entry in self.entries:
            if entry.diff_type == DiffType.ADDED:
                lines.append(f"  + [{entry.element_type}] {entry.name}"
                             + (f" ({entry.operator_type})" if entry.operator_type else ""))
                if entry.change_detail:
                    lines.append(f"      {entry.change_detail}")
            elif entry.diff_type == DiffType.REMOVED:
                lines.append(f"  - [{entry.element_type}] {entry.name}"
                             + (f" ({entry.operator_type})" if entry.operator_type else ""))
                if entry.change_detail:
                    lines.append(f"      {entry.change_detail}")
            elif entry.diff_type == DiffType.MODIFIED:
                lines.append(f"  ~ [{entry.element_type}] {entry.name}"
                             + (f" ({entry.operator_type})" if entry.operator_type else ""))
                if entry.change_detail:
                    lines.append(f"      {entry.change_detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def _operator_dict(op: Operator) -> dict:
    """Serialize an operator to a dict for comparison."""
    return op.model_dump()


def _describe_op_change(old: dict, new: dict) -> str:
    """
    Produce a human-readable description of what specifically changed
    between two operator dicts. Focuses on the most meaningful fields.
    """
    changes = []

    # Operator type changed
    if old.get("op") != new.get("op"):
        changes.append(f"operator type: {old.get('op')} -> {new.get('op')}")

    # Check common fields
    for field in ["input", "left", "right", "field", "to", "transform",
                  "condition", "type", "inputs", "group_by", "partition_by",
                  "order_by", "window_func"]:
        if field in old or field in new:
            old_val = old.get(field)
            new_val = new.get(field)
            if old_val != new_val:
                changes.append(f"{field}: {old_val} -> {new_val}")

    # Join keys — dig into the 'on' list
    if old.get("op") == "Join" and new.get("op") == "Join":
        old_on = old.get("on", [])
        new_on = new.get("on", [])
        if old_on != new_on:
            for i, (old_kp, new_kp) in enumerate(zip(old_on, new_on)):
                if old_kp != new_kp:
                    # Check for mapping changes specifically
                    old_map = old_kp.get("mapping")
                    new_map = new_kp.get("mapping")
                    if old_map != new_map:
                        # Find specific mapping entry changes
                        all_keys = set(list(old_map.keys()) if old_map else []) | \
                                   set(list(new_map.keys()) if new_map else [])
                        for mk in sorted(all_keys):
                            old_mv = old_map.get(mk) if old_map else None
                            new_mv = new_map.get(mk) if new_map else None
                            if old_mv != new_mv:
                                if old_mv is None:
                                    changes.append(f"join mapping added: '{mk}' -> '{new_mv}'")
                                elif new_mv is None:
                                    changes.append(f"join mapping removed: '{mk}' (was '{old_mv}')")
                                else:
                                    changes.append(f"join mapping changed: '{mk}': '{old_mv}' -> '{new_mv}'")
                    else:
                        changes.append(f"join key pair {i}: {old_kp} -> {new_kp}")
            if len(old_on) != len(new_on):
                changes.append(f"join key pair count: {len(old_on)} -> {len(new_on)}")

    # Aggregation specs
    if old.get("op") == "Aggregate" and new.get("op") == "Aggregate":
        old_agg = old.get("agg", [])
        new_agg = new.get("agg", [])
        if old_agg != new_agg:
            changes.append(f"aggregation specs: {old_agg} -> {new_agg}")

    if not changes:
        changes.append("parameters changed (see full diff for details)")

    return "; ".join(changes)


def _diff_input_sources(
    old_inputs: list[InputSource],
    new_inputs: list[InputSource],
) -> list[DiffEntry]:
    """Diff the input sources of two pipelines."""
    entries = []

    old_by_name = {s.name: s for s in old_inputs}
    new_by_name = {s.name: s for s in new_inputs}

    all_names = set(old_by_name.keys()) | set(new_by_name.keys())

    for name in sorted(all_names):
        if name in old_by_name and name not in new_by_name:
            entries.append(DiffEntry(
                diff_type=DiffType.REMOVED,
                element_type="input_source",
                name=name,
                old_value=old_by_name[name].model_dump(),
                change_detail=f"input source '{name}' removed",
            ))
        elif name not in old_by_name and name in new_by_name:
            entries.append(DiffEntry(
                diff_type=DiffType.ADDED,
                element_type="input_source",
                name=name,
                new_value=new_by_name[name].model_dump(),
                change_detail=f"input source '{name}' added",
            ))
        else:
            old_dict = old_by_name[name].model_dump()
            new_dict = new_by_name[name].model_dump()
            if old_dict != new_dict:
                changed_fields = [k for k in old_dict if old_dict[k] != new_dict.get(k)]
                entries.append(DiffEntry(
                    diff_type=DiffType.MODIFIED,
                    element_type="input_source",
                    name=name,
                    old_value=old_dict,
                    new_value=new_dict,
                    change_detail=f"input source modified: {', '.join(changed_fields)}",
                ))

    return entries


def diff_pipeline_irs(old: PipelineIR, new: PipelineIR) -> IRDiff:
    """
    Compute an operator-level diff between two PipelineIR objects.

    Operators are matched by their `output` name (the dataset they produce).
    - If an output name exists only in `new`: ADDED
    - If an output name exists only in `old`: REMOVED
    - If an output name exists in both but the operator dict differs: MODIFIED

    Input sources are diffed by name with the same logic.

    Returns an IRDiff object with all entries.
    """
    if old is None:
        raise ValueError("old PipelineIR must not be None — cannot diff against nothing")
    if new is None:
        raise ValueError("new PipelineIR must not be None — cannot diff nothing")

    entries: list[DiffEntry] = []

    # --- Diff input sources ---
    entries.extend(_diff_input_sources(old.inputs, new.inputs))

    # --- Diff operators ---
    old_ops_by_output: dict[str, Operator] = {op.output: op for op in old.steps}
    new_ops_by_output: dict[str, Operator] = {op.output: op for op in new.steps}

    all_outputs = set(old_ops_by_output.keys()) | set(new_ops_by_output.keys())

    for output_name in sorted(all_outputs):
        old_op = old_ops_by_output.get(output_name)
        new_op = new_ops_by_output.get(output_name)

        if old_op is None and new_op is not None:
            # Added
            entries.append(DiffEntry(
                diff_type=DiffType.ADDED,
                element_type="operator",
                name=output_name,
                operator_type=new_op.op,
                new_value=_operator_dict(new_op),
                change_detail=f"{new_op.op} operator added (output: {output_name})",
            ))
        elif old_op is not None and new_op is None:
            # Removed
            entries.append(DiffEntry(
                diff_type=DiffType.REMOVED,
                element_type="operator",
                name=output_name,
                operator_type=old_op.op,
                old_value=_operator_dict(old_op),
                change_detail=f"{old_op.op} operator removed (output: {output_name})",
            ))
        else:
            # Both exist — check if modified
            old_dict = _operator_dict(old_op)
            new_dict = _operator_dict(new_op)
            if old_dict != new_dict:
                change_detail = _describe_op_change(old_dict, new_dict)
                entries.append(DiffEntry(
                    diff_type=DiffType.MODIFIED,
                    element_type="operator",
                    name=output_name,
                    operator_type=new_op.op if new_op.op == old_op.op else f"{old_op.op}->{new_op.op}",
                    old_value=old_dict,
                    new_value=new_dict,
                    change_detail=change_detail,
                ))

    return IRDiff(
        old_name=old.name,
        new_name=new.name,
        entries=entries,
    )