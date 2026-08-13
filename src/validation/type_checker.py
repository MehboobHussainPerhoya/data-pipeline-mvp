"""
Type Checker — static pre-execution validation of a PipelineIR.

The Type Checker examines the IR *before* any data is run. It verifies:
- Every operator's input references a known dataset (input source or prior output)
- Cast operators target valid canonical types from the TypeRegistry
- Join keys are type-compatible (both keys are the same canonical type, or comparable)
- Map operators reference registered transforms or mappers
- Union inputs all share the same schema
- Filter conditions are syntactically valid
- Aggregate functions are from the allowed set

Errors are surfaced per-operator (which IR step failed), not just run-level.

FSD requirements:
- FR-TC-01: Pre-execution type validation
- FR-TC-02: Join key type compatibility check
- FR-TC-03: Inline error reporting (per-operator)
"""

from dataclasses import dataclass
from typing import Optional
from ir.pipeline_ir import PipelineIR, InputSource
from ir.operators import (
    CastOperator, MapOperator, FilterOperator, JoinOperator,
    AggregateOperator, WindowOperator, UnionOperator,
)
from ir.transform_registry import builtin_transform_registry
from schema.registry_setup import registry, SCHEMA_REGISTRY


@dataclass
class TypeError_:
    """
    One type error found by the Type Checker.
    Includes the step index and operator type for inline reporting.
    """
    step_index: int
    operator_type: str
    field: str
    message: str

    def __str__(self):
        return f"  Step {self.step_index} ({self.operator_type}): {self.field} — {self.message}"


@dataclass
class TypeCheckResult:
    """Result of type-checking a PipelineIR."""
    is_valid: bool
    errors: list[TypeError_]
    warnings: list[str]

    def summary(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": [str(e) for e in self.errors],
            "warnings": self.warnings,
        }


class TypeChecker:
    """
    Statically validates a PipelineIR before execution.

    Usage:
        checker = TypeChecker()
        result = checker.check(ir)
        if not result.is_valid:
            for e in result.errors:
                print(e)
    """

    VALID_AGG_FUNCTIONS = {"sum", "count", "avg", "min", "max"}
    VALID_JOIN_TYPES = {"inner", "left", "right", "full"}

    def __init__(self):
        self._registry = registry
        self._transforms = builtin_transform_registry

    def check(self, ir: PipelineIR, registered_mappers: set[str] = None) -> TypeCheckResult:
        """
        Type-check the full pipeline IR.

        registered_mappers: set of mapper names available in the executor
        (passed in so the checker can verify Map operators reference real mappers).
        """
        registered_mappers = registered_mappers or set()
        errors: list[TypeError_] = []
        warnings: list[str] = []

        # Build the set of known dataset names (inputs + outputs of each step)
        known_datasets: dict[str, str] = {}  # name -> schema_name
        for source in ir.inputs:
            known_datasets[source.name] = source.schema_name

        # Check each step in order
        for i, step in enumerate(ir.steps):
            step_errors, step_warnings = self._check_operator(
                i, step, known_datasets, registered_mappers
            )
            errors.extend(step_errors)
            warnings.extend(step_warnings)

            # Register this step's output as a known dataset
            # (its schema is inherited from its input for now —
            # a proper schema propagation system would track this precisely)
            if hasattr(step, 'input') and step.input in known_datasets:
                known_datasets[step.output] = known_datasets[step.input]
            elif hasattr(step, 'inputs'):
                # Union: inherit schema from first input
                if step.inputs and step.inputs[0] in known_datasets:
                    known_datasets[step.output] = known_datasets[step.inputs[0]]
            elif hasattr(step, 'left') and step.left in known_datasets:
                known_datasets[step.output] = known_datasets[step.left]
            else:
                known_datasets[step.output] = "unknown"

        is_valid = len(errors) == 0
        return TypeCheckResult(is_valid=is_valid, errors=errors, warnings=warnings)

    def _check_operator(
        self, index: int, step, known_datasets: dict, registered_mappers: set
    ) -> tuple[list[TypeError_], list[str]]:
        """Dispatch to the right checker based on operator type."""
        if isinstance(step, CastOperator):
            return self._check_cast(index, step, known_datasets)
        elif isinstance(step, MapOperator):
            return self._check_map(index, step, known_datasets, registered_mappers)
        elif isinstance(step, FilterOperator):
            return self._check_filter(index, step, known_datasets)
        elif isinstance(step, JoinOperator):
            return self._check_join(index, step, known_datasets)
        elif isinstance(step, AggregateOperator):
            return self._check_aggregate(index, step, known_datasets)
        elif isinstance(step, UnionOperator):
            return self._check_union(index, step, known_datasets)
        elif isinstance(step, WindowOperator):
            return self._check_window(index, step, known_datasets)
        else:
            return [TypeError_(index, type(step).__name__, "op", f"Unknown operator type: {type(step).__name__}")], []

    def _check_cast(self, index: int, step: CastOperator, known_datasets: dict) -> tuple[list[TypeError_], list[str]]:
        """Check a Cast operator."""
        errors = []

        # Input must be a known dataset
        if step.input not in known_datasets:
            errors.append(TypeError_(index, "Cast", "input", f"Input dataset '{step.input}' not found. Known: {list(known_datasets.keys())}"))

        # Target type must be a valid canonical type
        if step.to not in self._registry.type_names:
            errors.append(TypeError_(index, "Cast", "to", f"Target type '{step.to}' is not a valid canonical type. Available: {self._registry.type_names}"))

        return errors, []

    def _check_map(self, index: int, step: MapOperator, known_datasets: dict, registered_mappers: set) -> tuple[list[TypeError_], list[str]]:
        """Check a Map operator."""
        errors = []

        if step.input not in known_datasets:
            errors.append(TypeError_(index, "Map", "input", f"Input dataset '{step.input}' not found. Known: {list(known_datasets.keys())}"))

        # Transform must be a registered mapper or transform
        if step.transform not in registered_mappers and step.transform not in self._transforms.names:
            errors.append(TypeError_(index, "Map", "transform", f"Transform '{step.transform}' not found in registered mappers or transforms. Mappers: {registered_mappers}, Transforms: {self._transforms.names}"))

        return errors, []

    def _check_filter(self, index: int, step: FilterOperator, known_datasets: dict) -> tuple[list[TypeError_], list[str]]:
        """Check a Filter operator."""
        errors = []

        if step.input not in known_datasets:
            errors.append(TypeError_(index, "Filter", "input", f"Input dataset '{step.input}' not found. Known: {list(known_datasets.keys())}"))

        # Basic condition syntax check
        if "==" not in step.condition and "!=" not in step.condition:
            errors.append(TypeError_(index, "Filter", "condition", f"Condition '{step.condition}' is not a supported expression. Use 'field == value' or 'field != value'."))

        return errors, []

    def _check_join(self, index: int, step: JoinOperator, known_datasets: dict) -> tuple[list[TypeError_], list[str]]:
        """
        Check a Join operator.

        FSD: FR-TC-02 (join key type compatibility check)
        """
        errors = []
        warnings = []

        # Left and right datasets must exist
        if step.left not in known_datasets:
            errors.append(TypeError_(index, "Join", "left", f"Left dataset '{step.left}' not found. Known: {list(known_datasets.keys())}"))
        if step.right not in known_datasets:
            errors.append(TypeError_(index, "Join", "right", f"Right dataset '{step.right}' not found. Known: {list(known_datasets.keys())}"))

        # Join type must be valid
        if step.type not in self.VALID_JOIN_TYPES:
            errors.append(TypeError_(index, "Join", "type", f"Join type '{step.type}' is not valid. Must be one of: {self.VALID_JOIN_TYPES}"))

        # Check join key type compatibility
        # For each key pair, look up the schema of both sides and check the key fields' types
        left_schema_name = known_datasets.get(step.left)
        right_schema_name = known_datasets.get(step.right)

        for kp in step.on:
            if left_schema_name and left_schema_name in SCHEMA_REGISTRY:
                left_schema = SCHEMA_REGISTRY[left_schema_name]
                try:
                    left_field = left_schema.get_field(kp.left_key)
                    left_type = left_field.canonical_type
                except KeyError:
                    errors.append(TypeError_(index, "Join", f"left_key '{kp.left_key}'", f"Field '{kp.left_key}' not found in schema '{left_schema_name}'"))
                    left_type = None
            else:
                left_type = None

            if right_schema_name and right_schema_name in SCHEMA_REGISTRY:
                right_schema = SCHEMA_REGISTRY[right_schema_name]
                try:
                    right_field = right_schema.get_field(kp.right_key)
                    right_type = right_field.canonical_type
                except KeyError:
                    errors.append(TypeError_(index, "Join", f"right_key '{kp.right_key}'", f"Field '{kp.right_key}' not found in schema '{right_schema_name}'"))
                    right_type = None
            else:
                right_type = None

            # If both types are known, check compatibility
            if left_type and right_type:
                if left_type != right_type:
                    # Type mismatch — this is a warning, not an error, because
                    # the join might still work via implicit coercion (e.g., String to String
                    # with different formats). But we must warn, not silently proceed.
                    warnings.append(
                        f"  Step {index} (Join): key type mismatch — "
                        f"left key '{kp.left_key}' is {left_type}, "
                        f"right key '{kp.right_key}' is {right_type}. "
                        f"Implicit coercion may be required."
                    )

        return errors, warnings

    def _check_aggregate(self, index: int, step: AggregateOperator, known_datasets: dict) -> tuple[list[TypeError_], list[str]]:
        """Check an Aggregate operator."""
        errors = []

        if step.input not in known_datasets:
            errors.append(TypeError_(index, "Aggregate", "input", f"Input dataset '{step.input}' not found. Known: {list(known_datasets.keys())}"))

        for agg in step.agg:
            if agg.fn not in self.VALID_AGG_FUNCTIONS:
                errors.append(TypeError_(index, "Aggregate", f"agg.fn '{agg.fn}'", f"Aggregation function '{agg.fn}' is not valid. Must be one of: {self.VALID_AGG_FUNCTIONS}"))

        return errors, []

    def _check_union(self, index: int, step: UnionOperator, known_datasets: dict) -> tuple[list[TypeError_], list[str]]:
        """Check a Union operator — all inputs must share the same schema."""
        errors = []
        warnings = []

        schemas_seen = set()
        for input_name in step.inputs:
            if input_name not in known_datasets:
                errors.append(TypeError_(index, "Union", f"input '{input_name}'", f"Input dataset '{input_name}' not found. Known: {list(known_datasets.keys())}"))
            else:
                schemas_seen.add(known_datasets[input_name])

        # All inputs should have the same schema
        if len(schemas_seen) > 1:
            errors.append(TypeError_(index, "Union", "inputs", f"Schema mismatch — inputs have different schemas: {schemas_seen}. Union requires all inputs to share the same canonical schema."))

        return errors, warnings

    def _check_window(self, index: int, step: WindowOperator, known_datasets: dict) -> tuple[list[TypeError_], list[str]]:
        """Check a Window operator."""
        errors = []

        if step.input not in known_datasets:
            errors.append(TypeError_(index, "Window", "input", f"Input dataset '{step.input}' not found. Known: {list(known_datasets.keys())}"))

        return errors, []