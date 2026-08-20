"""
Run health dashboard — FR-MON-01 (Run health dashboard).

Reads from BuildScheduler's BuildResult history (Phase 9) and the deploy
audit log (deploy_audit_log.txt) to expose run status, duration, and
row-count trends per pipeline.

This module is OBSERVATIONAL ONLY — it reads what the scheduler and deploy
gate already record. It does not duplicate run-tracking logic, does not
run builds, and does not deploy.

FSD requirement:
- FR-MON-01: System shall expose run status, duration, and row-count
  trends per pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from deployment.scheduler import BuildResult


@dataclass
class RunSummary:
    """
    Summary of a single run (build) for the health dashboard.

    Attributes:
        run_id: a stable identifier for this run
        pipeline_name: the schedule/trigger name that produced this run
        trigger_type: scheduled, event, or manual
        status: success, validation_failed, or build_failed
        started_at: ISO timestamp when the build started
        completed_at: ISO timestamp when the build completed
        duration_seconds: elapsed time (completed - started)
        record_count: number of output records built
        error_count: number of build errors
        is_safe: whether validation passed
    """
    run_id: str
    pipeline_name: str
    trigger_type: str
    status: str
    started_at: str
    completed_at: str
    duration_seconds: float
    record_count: int
    error_count: int
    is_safe: bool

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "pipeline_name": self.pipeline_name,
            "trigger_type": self.trigger_type,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "record_count": self.record_count,
            "error_count": self.error_count,
            "is_safe": self.is_safe,
        }


@dataclass
class RunTrend:
    """
    Aggregated trend across multiple runs of a pipeline.

    Attributes:
        pipeline_name: the schedule/trigger name
        total_runs: number of runs observed
        success_count: how many succeeded
        failure_count: how many failed (validation_failed + build_failed)
        success_rate: success_count / total_runs (0.0 if no runs)
        avg_duration_seconds: mean duration across all runs
        avg_record_count: mean record count across successful runs
        latest_status: status of the most recent run
        latest_record_count: record count of the most recent run
        record_count_history: list of (run_id, record_count) in chronological order
        duration_history: list of (run_id, duration_seconds) in chronological order
    """
    pipeline_name: str
    total_runs: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_duration_seconds: float
    avg_record_count: float
    latest_status: str
    latest_record_count: int
    record_count_history: list[tuple[str, int]] = field(default_factory=list)
    duration_history: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pipeline_name": self.pipeline_name,
            "total_runs": self.total_runs,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(self.success_rate, 4),
            "avg_duration_seconds": round(self.avg_duration_seconds, 3),
            "avg_record_count": round(self.avg_record_count, 1),
            "latest_status": self.latest_status,
            "latest_record_count": self.latest_record_count,
            "record_count_history": [
                {"run_id": rid, "record_count": rc} for rid, rc in self.record_count_history
            ],
            "duration_history": [
                {"run_id": rid, "duration_seconds": round(d, 3)} for rid, d in self.duration_history
            ],
        }


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO timestamp string, returning None on failure."""
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _compute_duration_seconds(started_at: str, completed_at: str) -> float:
    """Compute duration in seconds between two ISO timestamps."""
    start = _parse_iso(started_at)
    end = _parse_iso(completed_at)
    if start is None or end is None:
        return 0.0
    return (end - start).total_seconds()


class RunHealthDashboard:
    """
    Reads BuildResult history from a BuildScheduler and exposes run health
    summaries and trends per pipeline (FR-MON-01).

    This is a READ-ONLY view over existing scheduler data — it does not
    trigger builds, deploy, or modify any state.

    Usage:
        dashboard = RunHealthDashboard(build_scheduler)
        summaries = dashboard.get_all_runs()
        trend = dashboard.get_trend("hourly_build")
        all_trends = dashboard.get_all_trends()
    """

    def __init__(self, build_scheduler):
        """
        Args:
            build_scheduler: a BuildScheduler instance whose get_build_results()
                returns the BuildResult history to analyze.
        """
        self._scheduler = build_scheduler

    def get_all_runs(self) -> list[RunSummary]:
        """
        Return a RunSummary for every build result in the scheduler's history,
        in chronological order (oldest first).

        Each BuildResult is converted to a RunSummary with a computed duration.
        """
        results = self._scheduler.get_build_results()
        summaries = []
        for i, result in enumerate(results):
            run_id = f"run_{i + 1:04d}"
            duration = _compute_duration_seconds(result.started_at, result.completed_at)
            summaries.append(RunSummary(
                run_id=run_id,
                pipeline_name=result.trigger_name,
                trigger_type=result.trigger_type,
                status=result.status,
                started_at=result.started_at,
                completed_at=result.completed_at,
                duration_seconds=duration,
                record_count=result.record_count,
                error_count=result.error_count,
                is_safe=result.is_safe,
            ))
        return summaries

    def get_runs_for_pipeline(self, pipeline_name: str) -> list[RunSummary]:
        """Return RunSummaries for a specific pipeline (trigger name) only."""
        return [s for s in self.get_all_runs() if s.pipeline_name == pipeline_name]

    def get_latest_run(self) -> Optional[RunSummary]:
        """Return the most recent run, or None if no builds have run."""
        runs = self.get_all_runs()
        if not runs:
            return None
        return runs[-1]

    def get_trend(self, pipeline_name: str) -> Optional[RunTrend]:
        """
        Compute a trend summary across all runs of a specific pipeline.

        Returns None if no runs exist for the given pipeline name.
        """
        runs = self.get_runs_for_pipeline(pipeline_name)
        if not runs:
            return None
        return self._compute_trend(pipeline_name, runs)

    def get_all_trends(self) -> list[RunTrend]:
        """
        Compute trend summaries for every pipeline that has at least one run.

        Returns a list of RunTrend, one per unique pipeline_name, in
        alphabetical order by pipeline name.
        """
        runs = self.get_all_runs()
        pipeline_names = sorted(set(r.pipeline_name for r in runs))
        trends = []
        for name in pipeline_names:
            pipeline_runs = [r for r in runs if r.pipeline_name == name]
            trend = self._compute_trend(name, pipeline_runs)
            trends.append(trend)
        return trends

    def _compute_trend(self, pipeline_name: str, runs: list[RunSummary]) -> RunTrend:
        """Compute a RunTrend from a list of RunSummaries."""
        total = len(runs)
        success_count = sum(1 for r in runs if r.status == "success")
        failure_count = total - success_count
        success_rate = success_count / total if total > 0 else 0.0

        durations = [r.duration_seconds for r in runs]
        avg_duration = sum(durations) / total if total > 0 else 0.0

        successful_runs = [r for r in runs if r.status == "success"]
        if successful_runs:
            avg_record_count = sum(r.record_count for r in successful_runs) / len(successful_runs)
        else:
            avg_record_count = 0.0

        latest = runs[-1]

        return RunTrend(
            pipeline_name=pipeline_name,
            total_runs=total,
            success_count=success_count,
            failure_count=failure_count,
            success_rate=success_rate,
            avg_duration_seconds=avg_duration,
            avg_record_count=avg_record_count,
            latest_status=latest.status,
            latest_record_count=latest.record_count,
            record_count_history=[(r.run_id, r.record_count) for r in runs],
            duration_history=[(r.run_id, r.duration_seconds) for r in runs],
        )

    def get_deploy_history(self, audit_log_path: str) -> list[dict]:
        """
        Read the deploy audit log and return parsed deploy entries.

        Each entry has: timestamp, record_count, output_path, approved,
        and optional version/deployed_by fields.

        This reads the existing deploy_audit_log.txt written by deploy_gate.py
        — it does not modify it.
        """
        path = Path(audit_log_path)
        if not path.exists():
            return []

        entries = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            entry = self._parse_audit_line(line)
            if entry:
                entries.append(entry)
        return entries

    def _parse_audit_line(self, line: str) -> Optional[dict]:
        """Parse one line of deploy_audit_log.txt into a dict."""
        parts = [p.strip() for p in line.split("|")]
        if not parts:
            return None

        entry = {}
        # First part is always the timestamp
        entry["timestamp"] = parts[0]

        for part in parts[1:]:
            if part.startswith("Deployed ") and " records to " in part:
                # "Deployed 8669 records to /path/to/output.json"
                try:
                    count_str = part.replace("Deployed ", "").split(" records to ")[0]
                    entry["record_count"] = int(count_str)
                    entry["output_path"] = part.split(" records to ")[1]
                except (ValueError, IndexError):
                    pass
            elif part.startswith("approved="):
                entry["approved"] = part.split("=", 1)[1]
            elif part.startswith("version="):
                entry["version"] = part.split("=", 1)[1]
            elif part.startswith("deployed_by="):
                entry["deployed_by"] = part.split("=", 1)[1]

        return entry

    def summary(self) -> dict:
        """Return a high-level summary of run health across all pipelines."""
        runs = self.get_all_runs()
        trends = self.get_all_trends()
        return {
            "total_runs": len(runs),
            "pipeline_count": len(trends),
            "pipelines": [t.to_dict() for t in trends],
        }
