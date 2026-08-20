"""
SLA/failure alerting — FR-MON-03 (SLA/failure alerting).

Detects alert conditions (run failures and SLA breaches) from BuildScheduler
history, generates real AlertRecord objects (owner, reason, timestamp,
severity), and makes them retrievable via a stub notification channel.

This module is OBSERVATIONAL ONLY — it reads BuildResult history and evaluates
alert conditions. It does not run builds, deploy, or modify pipeline state.

The notification channel is a STUB — it stores alerts in an in-memory list
and optionally writes them to a file. It does NOT send emails or Slack
messages. This is clearly labeled as a stub per the FSD MVP scope. The alert
DETECTION and RECORD GENERATION logic is real and fully testable.

FSD requirement:
- FR-MON-03: System shall notify designated owners on run failure or SLA
  breach via configured channels.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
import json

from deployment.scheduler import BuildResult


class AlertType(str, Enum):
    """The kind of alert condition that was detected."""
    RUN_FAILURE = "run_failure"
    SLA_BREACH = "sla_breach"


class AlertSeverity(str, Enum):
    """Severity level of an alert."""
    CRITICAL = "critical"    # run failure — pipeline did not produce valid output
    WARNING = "warning"      # SLA breach — pipeline ran but exceeded a threshold


@dataclass
class SLAConfig:
    """
    SLA configuration for a pipeline (schedule/trigger name).

    Attributes:
        pipeline_name: the schedule/trigger name this SLA applies to
        max_duration_seconds: if a run exceeds this duration, it's an SLA breach
        owner: the designated owner to notify (email, name, or team identifier)
        enabled: whether this SLA is active
    """
    pipeline_name: str
    max_duration_seconds: float
    owner: str = "pipeline-owner"
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "pipeline_name": self.pipeline_name,
            "max_duration_seconds": self.max_duration_seconds,
            "owner": self.owner,
            "enabled": self.enabled,
        }


@dataclass
class AlertRecord:
    """
    A real alert record generated when an alert condition is detected.

    Attributes:
        alert_id: stable unique identifier
        alert_type: run_failure or sla_breach
        severity: critical or warning
        pipeline_name: the schedule/trigger name that triggered the alert
        run_id: identifier of the specific run that triggered the alert
        owner: designated owner to notify
        reason: human-readable explanation of why the alert fired
        timestamp: when the alert was generated (ISO UTC)
        run_started_at: when the triggering run started
        run_duration_seconds: duration of the triggering run (0 for build failures)
        run_status: status of the triggering run
        details: additional structured details about the condition
    """
    alert_id: str
    alert_type: str
    severity: str
    pipeline_name: str
    run_id: str
    owner: str
    reason: str
    timestamp: str
    run_started_at: str
    run_duration_seconds: float
    run_status: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "pipeline_name": self.pipeline_name,
            "run_id": self.run_id,
            "owner": self.owner,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "run_started_at": self.run_started_at,
            "run_duration_seconds": round(self.run_duration_seconds, 3),
            "run_status": self.run_status,
            "details": self.details,
        }


class StubNotificationChannel:
    """
    STUB notification channel — stores alerts in an in-memory list and
    optionally writes them to a JSON file.

    This is NOT a real notification mechanism (no email/Slack integration).
    It is clearly labeled as a stub per the FSD MVP scope. The alert
    detection and record generation logic in AlertEngine is real and
    fully testable; this channel just provides a retrievable storage
    mechanism for generated alerts.

    A production system would replace this with real channel implementations
    (email, Slack, PagerDuty, etc.) that implement the same interface:
    send(alert: AlertRecord) -> bool and get_alerts() -> list[AlertRecord].
    """

    def __init__(self, file_path: str | None = None):
        """
        Args:
            file_path: optional path to persist alerts as JSON. If None,
                alerts are kept only in memory.
        """
        self._alerts: list[AlertRecord] = []
        self._file_path = file_path

    def send(self, alert: AlertRecord) -> bool:
        """
        Store an alert record (stub — does not send a real notification).

        Returns True to indicate the alert was "delivered" (stored).
        """
        self._alerts.append(alert)
        if self._file_path:
            self._persist()
        return True

    def get_alerts(self) -> list[AlertRecord]:
        """Retrieve all generated alert records."""
        return list(self._alerts)

    def get_alerts_for_pipeline(self, pipeline_name: str) -> list[AlertRecord]:
        """Retrieve alerts for a specific pipeline only."""
        return [a for a in self._alerts if a.pipeline_name == pipeline_name]

    def get_alerts_by_severity(self, severity: str) -> list[AlertRecord]:
        """Retrieve alerts filtered by severity."""
        return [a for a in self._alerts if a.severity == severity]

    def clear(self) -> None:
        """Clear all stored alerts."""
        self._alerts = []
        if self._file_path:
            self._persist()

    def count(self) -> int:
        """Return the number of stored alerts."""
        return len(self._alerts)

    def _persist(self) -> None:
        """Write alerts to the configured file path as JSON."""
        if not self._file_path:
            return
        path = Path(self._file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([a.to_dict() for a in self._alerts], f, indent=2)

    def summary(self) -> dict:
        return {
            "channel_type": "stub",
            "total_alerts": len(self._alerts),
            "critical_count": sum(1 for a in self._alerts if a.severity == AlertSeverity.CRITICAL.value),
            "warning_count": sum(1 for a in self._alerts if a.severity == AlertSeverity.WARNING.value),
            "file_path": self._file_path,
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


class AlertEngine:
    """
    Detects alert conditions from BuildScheduler history and generates
    real AlertRecord objects (FR-MON-03).

    Two alert conditions are detected:
    1. RUN_FAILURE: a build result with status "validation_failed" or
       "build_failed" — the pipeline did not produce valid output.
    2. SLA_BREACH: a build result whose duration exceeds the configured
       max_duration_seconds for that pipeline.

    The engine is READ-ONLY over scheduler data — it does not run builds
    or deploy. It generates alert records and sends them through a
    notification channel (stub by default).

    Usage:
        engine = AlertEngine(channel=StubNotificationChannel())
        engine.add_sla(SLAConfig("hourly_build", max_duration_seconds=300, owner="alice"))
        alerts = engine.evaluate(scheduler)
        all_alerts = engine.get_alerts()
    """

    def __init__(self, channel: StubNotificationChannel | None = None):
        """
        Args:
            channel: the notification channel to send alerts through.
                If None, a default in-memory stub channel is created.
        """
        self._channel = channel if channel is not None else StubNotificationChannel()
        self._sla_configs: dict[str, SLAConfig] = {}
        self._evaluated_run_ids: set[str] = set()
        self._alert_counter = 0

    @property
    def channel(self) -> StubNotificationChannel:
        """Access the notification channel."""
        return self._channel

    def add_sla(self, config: SLAConfig) -> None:
        """Register an SLA configuration for a pipeline."""
        self._sla_configs[config.pipeline_name] = config

    def get_sla(self, pipeline_name: str) -> Optional[SLAConfig]:
        """Retrieve the SLA config for a pipeline, or None if not configured."""
        return self._sla_configs.get(pipeline_name)

    def list_slas(self) -> list[SLAConfig]:
        """List all registered SLA configurations."""
        return list(self._sla_configs.values())

    def evaluate(self, build_scheduler) -> list[AlertRecord]:
        """
        Evaluate all build results in the scheduler's history for alert
        conditions. Generates AlertRecords for any new failures or SLA
        breaches found since the last evaluation.

        This method is idempotent within a session — it tracks which run_ids
        have already been evaluated and will not generate duplicate alerts
        for the same run on repeated calls. Call reset() to re-evaluate all.

        Args:
            build_scheduler: a BuildScheduler instance

        Returns a list of newly generated AlertRecords (may be empty).
        """
        results = build_scheduler.get_build_results()
        new_alerts: list[AlertRecord] = []

        for i, result in enumerate(results):
            run_id = f"run_{i + 1:04d}"
            if run_id in self._evaluated_run_ids:
                continue
            self._evaluated_run_ids.add(run_id)

            # Check for run failure
            failure_alert = self._check_run_failure(result, run_id)
            if failure_alert:
                new_alerts.append(failure_alert)
                self._channel.send(failure_alert)

            # Check for SLA breach
            sla_alert = self._check_sla_breach(result, run_id)
            if sla_alert:
                new_alerts.append(sla_alert)
                self._channel.send(sla_alert)

        return new_alerts

    def _check_run_failure(self, result: BuildResult, run_id: str) -> Optional[AlertRecord]:
        """
        Check if a build result represents a run failure.

        A run failure is any status other than "success" — either
        "validation_failed" or "build_failed".
        """
        if result.status == "success":
            return None

        # Determine the owner from SLA config if available, else default
        sla = self._sla_configs.get(result.trigger_name)
        owner = sla.owner if sla else "pipeline-owner"

        # Build a descriptive reason
        if result.status == "build_failed":
            reason = f"Build '{result.trigger_name}' failed: {result.error_message}"
        elif result.status == "validation_failed":
            reasons_str = "; ".join(result.safety_reasons) if result.safety_reasons else "validation failed"
            reason = f"Build '{result.trigger_name}' failed validation: {reasons_str}"
        else:
            reason = f"Build '{result.trigger_name}' has abnormal status: {result.status}"

        self._alert_counter += 1
        return AlertRecord(
            alert_id=f"alert_{self._alert_counter:04d}",
            alert_type=AlertType.RUN_FAILURE.value,
            severity=AlertSeverity.CRITICAL.value,
            pipeline_name=result.trigger_name,
            run_id=run_id,
            owner=owner,
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
            run_started_at=result.started_at,
            run_duration_seconds=_compute_duration_seconds(result.started_at, result.completed_at),
            run_status=result.status,
            details={
                "error_count": result.error_count,
                "safety_reasons": result.safety_reasons,
                "error_message": result.error_message,
            },
        )

    def _check_sla_breach(self, result: BuildResult, run_id: str) -> Optional[AlertRecord]:
        """
        Check if a build result represents an SLA breach (duration exceeded).

        Only checks SLA for pipelines that have a configured SLAConfig.
        Only checks successful runs — a failed run already generates a
        run_failure alert; an SLA breach on a failed run would be redundant.
        """
        sla = self._sla_configs.get(result.trigger_name)
        if sla is None or not sla.enabled:
            return None

        # Only check SLA on successful runs — failures already get a critical alert
        if result.status != "success":
            return None

        duration = _compute_duration_seconds(result.started_at, result.completed_at)

        if duration <= sla.max_duration_seconds:
            return None

        self._alert_counter += 1
        return AlertRecord(
            alert_id=f"alert_{self._alert_counter:04d}",
            alert_type=AlertType.SLA_BREACH.value,
            severity=AlertSeverity.WARNING.value,
            pipeline_name=result.trigger_name,
            run_id=run_id,
            owner=sla.owner,
            reason=(
                f"Build '{result.trigger_name}' exceeded SLA: "
                f"{duration:.1f}s > {sla.max_duration_seconds:.1f}s threshold"
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
            run_started_at=result.started_at,
            run_duration_seconds=duration,
            run_status=result.status,
            details={
                "max_duration_seconds": sla.max_duration_seconds,
                "actual_duration_seconds": duration,
                "record_count": result.record_count,
            },
        )

    def get_alerts(self) -> list[AlertRecord]:
        """Retrieve all generated alert records from the channel."""
        return self._channel.get_alerts()

    def get_alerts_for_pipeline(self, pipeline_name: str) -> list[AlertRecord]:
        """Retrieve alerts for a specific pipeline."""
        return self._channel.get_alerts_for_pipeline(pipeline_name)

    def reset(self) -> None:
        """Clear evaluated run tracking and all stored alerts."""
        self._evaluated_run_ids.clear()
        self._channel.clear()
        self._alert_counter = 0

    def summary(self) -> dict:
        """Return a summary of the alert engine state."""
        alerts = self.get_alerts()
        return {
            "total_alerts": len(alerts),
            "run_failure_count": sum(1 for a in alerts if a.alert_type == AlertType.RUN_FAILURE.value),
            "sla_breach_count": sum(1 for a in alerts if a.alert_type == AlertType.SLA_BREACH.value),
            "critical_count": sum(1 for a in alerts if a.severity == AlertSeverity.CRITICAL.value),
            "warning_count": sum(1 for a in alerts if a.severity == AlertSeverity.WARNING.value),
            "sla_configs": [s.to_dict() for s in self._sla_configs.values()],
            "channel": self._channel.summary(),
        }
