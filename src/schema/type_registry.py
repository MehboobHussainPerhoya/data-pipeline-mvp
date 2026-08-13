"""
TypeRegistry — the versioned, extensible catalog of canonical types.

The registry is the single source of truth for what types exist in the pipeline.
It is versioned: every change (add/modify a type) bumps the version, so
existing pipelines can pin to a specific version and not silently break.

FSD requirements:
- FR-REG-01: Canonical type catalog
- FR-REG-03: Registry versioning
- FR-REG-04: Custom type extension
"""

from dataclasses import dataclass, field
from typing import Optional
from .canonical_types import CanonicalType, BUILTIN_TYPES


@dataclass
class RegistryVersion:
    """Snapshot of the registry at a point in time."""
    version: int
    types: dict[str, CanonicalType]  # name -> type definition


class TypeRegistry:
    """
    Holds and versions canonical type definitions.

    Usage:
        registry = TypeRegistry()  # starts with built-in types at version 1
        registry.get_type("String")  # retrieve a type by name
        registry.register_type(my_custom_type)  # adds type, bumps version
        registry.get_version(1)  # retrieve a historical snapshot
    """

    def __init__(self):
        self._current_types: dict[str, CanonicalType] = {}
        self._versions: list[RegistryVersion] = []

        # Load built-in types
        for t in BUILTIN_TYPES:
            self._current_types[t.name] = t

        # Snapshot version 1 (built-in types only)
        self._snapshot_version()

    @property
    def version(self) -> int:
        """Current registry version number."""
        return len(self._versions)

    @property
    def type_names(self) -> list[str]:
        """Names of all types in the current version."""
        return list(self._current_types.keys())

    def get_type(self, name: str) -> CanonicalType:
        """
        Retrieve a canonical type by name from the current version.
        Raises KeyError if not found.
        """
        if name not in self._current_types:
            raise KeyError(
                f"Type '{name}' not found in registry (v{self.version}). "
                f"Available types: {self.type_names}"
            )
        return self._current_types[name]

    def register_type(self, canonical_type: CanonicalType) -> int:
        """
        Register a new canonical type (or replace an existing one).
        Creates a new registry version. Returns the new version number.

        FSD: FR-REG-04 (custom type extension)
        """
        self._current_types[canonical_type.name] = canonical_type
        new_version = self._snapshot_version()
        return new_version

    def get_version(self, version: int) -> RegistryVersion:
        """
        Retrieve a historical snapshot of the registry.
        Useful for pipelines pinned to an older version.

        FSD: FR-REG-03 (registry versioning)
        """
        if version < 1 or version > len(self._versions):
            raise ValueError(f"Version {version} does not exist. Valid range: 1-{len(self._versions)}")
        return self._versions[version - 1]

    def is_operation_valid(self, type_name: str, operation: str) -> bool:
        """
        Check whether an operation is valid for a given canonical type.

        FSD: FR-REG-02 (type-scoped operation catalog)
        """
        t = self.get_type(type_name)
        return operation in t.valid_operations

    def _snapshot_version(self) -> int:
        """Take a snapshot of the current types and store it as a new version."""
        snapshot = RegistryVersion(
            version=len(self._versions) + 1,
            types=dict(self._current_types),  # shallow copy of the dict
        )
        self._versions.append(snapshot)
        return snapshot.version

    def summary(self) -> dict:
        """Return a summary dict suitable for MCP tool output."""
        return {
            "current_version": self.version,
            "type_count": len(self._current_types),
            "type_names": self.type_names,
        }