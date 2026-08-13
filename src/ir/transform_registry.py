"""
TransformRegistry — maps named transforms to their Python implementations.

The IR's MapOperator references transforms by name (e.g., "trim_whitespace").
This registry resolves those names to actual functions at execution time.
This keeps the IR declarative (what to do) while the registry holds
the implementation (how to do it).

New transforms can be registered without changing the IR or executor.
"""

from typing import Callable, Any


class TransformRegistry:
    """
    Holds named transform functions that MapOperator instances reference.

    Usage:
        tr = TransformRegistry()
        tr.register("trim", lambda v: v.strip() if v else v)
        tr.apply("trim", "  hello  ")  # -> "hello"
    """

    def __init__(self):
        self._transforms: dict[str, Callable[[Any], Any]] = {}

    def register(self, name: str, func: Callable[[Any], Any]) -> None:
        """Register a named transform function."""
        self._transforms[name] = func

    def get(self, name: str) -> Callable[[Any], Any]:
        """Retrieve a transform function by name."""
        if name not in self._transforms:
            raise KeyError(f"Transform '{name}' not found. Available: {list(self._transforms.keys())}")
        return self._transforms[name]

    def apply(self, name: str, value: Any, params: dict = None) -> Any:
        """Apply a named transform to a value."""
        func = self.get(name)
        if params:
            return func(value, **params)
        return func(value)

    @property
    def names(self) -> list[str]:
        return list(self._transforms.keys())


# ---------------------------------------------------------------------------
# Built-in transforms — the standard set available in every pipeline
# ---------------------------------------------------------------------------

import json
from datetime import datetime


def _trim_whitespace(value):
    if value is None or value == "":
        return None
    return str(value).strip()


def _empty_to_none(value):
    if value is None or value == "":
        return None
    return value


def _parse_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    # Try common formats
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None  # couldn't parse — return None rather than crashing


def _prefix_id(value, prefix=""):
    """Prefix an ID with a source name (e.g., 'ticket_1')."""
    if value is None:
        return None
    return f"{prefix}{value}"


# Create the global transform registry with built-in transforms
builtin_transform_registry = TransformRegistry()
builtin_transform_registry.register("trim_whitespace", _trim_whitespace)
builtin_transform_registry.register("empty_to_none", _empty_to_none)
builtin_transform_registry.register("parse_timestamp", _parse_timestamp)
builtin_transform_registry.register("prefix_id", _prefix_id)