"""
PipelineIR — the engine-agnostic logical plan.

A PipelineIR holds:
- Named input sources (what data comes in)
- An ordered list of IR operators (what happens to the data)
- An output contract reference (what the result must satisfy)

This is what the Type Checker validates, the Executor runs, and the
Agent Orchestrator proposes operators into.

FSD requirements: FR-IR-01 (canonical operator set), FR-IR-02 (engine compilation targets)
"""

from pydantic import BaseModel
from typing import Optional
from .operators import (
    CastOperator, MapOperator, FilterOperator, JoinOperator,
    AggregateOperator, WindowOperator, UnionOperator, Operator,
    OPERATOR_TYPES,
)


class InputSource(BaseModel):
    """Declaration of a named input source for the pipeline."""
    name: str                    # dataset name used in operators (e.g., "tickets")
    source_type: str             # "csv", "api", "rdbms", "kafka"
    location: str                # file path, URL, connection string
    schema_name: str             # canonical schema name from SchemaRegistry
    connector: str               # connector function name to call for ingestion


class PipelineIR(BaseModel):
    """
    The complete, engine-agnostic logical plan for a pipeline.

    Example:
        ir = PipelineIR(
            name="support_case_pipeline",
            inputs=[
                InputSource(name="tickets", source_type="csv", ...),
                InputSource(name="kb", source_type="csv", ...),
                InputSource(name="api", source_type="api", ...),
            ],
            steps=[
                CastOperator(op="Cast", input="tickets", field="price", to="Double", output="tickets_cast"),
                JoinOperator(op="Join", left="tickets", right="kb", on=[...], output="joined"),
                ...
            ],
            output_contract="JoinedCaseOutput",
        )
    """
    name: str
    inputs: list[InputSource] = []
    steps: list[Operator] = []
    output_contract: str = ""        # schema name the output must satisfy

    def step_count(self) -> int:
        return len(self.steps)

    def get_step(self, index: int) -> Operator:
        """Retrieve a step by index."""
        if index < 0 or index >= len(self.steps):
            raise IndexError(f"Step index {index} out of range (0-{len(self.steps)-1})")
        return self.steps[index]

    def add_step(self, step: Operator) -> None:
        """Append a step to the pipeline."""
        self.steps.append(step)

    def operator_types_used(self) -> list[str]:
        """Return the distinct operator types used in this pipeline."""
        return list(dict.fromkeys(s.op for s in self.steps))

    def to_summary(self) -> dict:
        """Return a human-readable summary suitable for MCP tool output."""
        return {
            "name": self.name,
            "input_count": len(self.inputs),
            "inputs": [{"name": i.name, "type": i.source_type, "schema": i.schema_name} for i in self.inputs],
            "step_count": len(self.steps),
            "operators": [f"{i}: {s.op}({s.output})" for i, s in enumerate(self.steps)],
            "output_contract": self.output_contract,
        }

    def to_json(self) -> str:
        """Serialize the full IR to JSON — for storage, diffing, or MCP export."""
        return self.model_dump_json(indent=2)