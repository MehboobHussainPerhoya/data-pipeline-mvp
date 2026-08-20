"""
Ingestion failure isolation — FR-ING-05.

A failure in one source connector must not block ingestion from other
independent sources in the same run. This module provides:
  - IngestionResult: per-source result (success or failure with error detail)
  - ingest_with_isolation: wraps a connector call in try/except, returning
    a result object instead of raising
  - run_ingestion_isolated: runs all sources with isolation, returns a
    summary of successes and failures

This is the reliability fix — the single most important change in Phase 12.
"""

from datetime import datetime, timezone
from typing import Callable, Any, Optional
from pydantic import BaseModel, Field


class IngestionResult(BaseModel):
    """Result of ingesting one source — success or failure."""
    source_name: str
    success: bool
    record_count: int = 0
    records: list[Any] = Field(default_factory=list)  # only populated on success
    error: Optional[str] = None                        # only populated on failure
    error_type: Optional[str] = None                   # exception class name
    duration_seconds: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> dict:
        return {
            "source_name": self.source_name,
            "success": self.success,
            "record_count": self.record_count,
            "error": self.error,
            "error_type": self.error_type,
            "duration_seconds": round(self.duration_seconds, 3),
        }


def ingest_with_isolation(
    source_name: str,
    connector_fn: Callable,
    *args,
    **kwargs,
) -> IngestionResult:
    """
    Call a connector function in isolation. If it raises, the exception is
    captured in the result — not propagated. Other sources can still proceed.

    Returns an IngestionResult with either records (success) or error detail (failure).
    """
    import time
    start = time.time()
    try:
        records = connector_fn(*args, **kwargs)
        duration = time.time() - start
        return IngestionResult(
            source_name=source_name,
            success=True,
            record_count=len(records),
            records=records,
            duration_seconds=duration,
        )
    except Exception as e:
        duration = time.time() - start
        return IngestionResult(
            source_name=source_name,
            success=False,
            error=str(e),
            error_type=type(e).__name__,
            duration_seconds=duration,
        )


class IngestionRunResult(BaseModel):
    """Aggregate result of ingesting all sources in a run."""
    results: list[IngestionResult] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def successful_sources(self) -> list[str]:
        return [r.source_name for r in self.results if r.success]

    @property
    def failed_sources(self) -> list[str]:
        return [r.source_name for r in self.results if not r.success]

    @property
    def all_succeeded(self) -> bool:
        return all(r.success for r in self.results) if self.results else False

    @property
    def any_succeeded(self) -> bool:
        return any(r.success for r in self.results)

    def get_records(self, source_name: str) -> list[Any]:
        """Get the records from a successful source, or [] if it failed."""
        for r in self.results:
            if r.source_name == source_name and r.success:
                return r.records
        return []

    def summary(self) -> dict:
        return {
            "total_sources": len(self.results),
            "successful": len(self.successful_sources),
            "failed": len(self.failed_sources),
            "successful_sources": self.successful_sources,
            "failed_sources": self.failed_sources,
            "all_succeeded": self.all_succeeded,
            "any_succeeded": self.any_succeeded,
            "results": [r.summary() for r in self.results],
        }


def run_ingestion_isolated(
    sources: list[tuple[str, Callable, tuple, dict]],
) -> IngestionRunResult:
    """
    Run ingestion for multiple sources with failure isolation.

    Each source is a tuple of (source_name, connector_fn, args_tuple, kwargs_dict).
    A failure in one source does not block the others.

    Returns an IngestionRunResult with per-source results.
    """
    results = []
    for source_name, connector_fn, args, kwargs in sources:
        result = ingest_with_isolation(source_name, connector_fn, *args, **kwargs)
        results.append(result)
    return IngestionRunResult(results=results)
