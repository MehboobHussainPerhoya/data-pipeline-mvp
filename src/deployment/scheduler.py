"""
Build scheduler — FR-DEPLOY-02 (Scheduled & triggered builds).

Supports both scheduled (cron-like) and event-triggered pipeline BUILDS.
A build = run pipeline + validate. A build NEVER deploys on its own.

Critical invariant: the scheduler has no reference to deploy_gate.py and no
deploy method. A scheduled/triggered build produces a candidate output
(stored in BuildResult) that must still pass through deploy_gate.py's
approval gate (is_safe=True AND approved=True) before anything is actually
deployed. Scheduling automates the build, never the approval.

Design:
- BuildScheduler holds scheduled jobs (cron-like) and event triggers.
- Each job/trigger references a build_fn: a callable that runs the pipeline
  and returns (output_records, errors). The scheduler then validates the
  result using is_safe_to_deploy() and stores it as a BuildResult.
- check_schedules() evaluates whether any cron schedule is due at a given
  time and runs the matching builds.
- trigger_event() fires an event-triggered build immediately.
- get_build_results() / get_latest_build() return candidate outputs for
  review — never deployed output.

Cron expressions:
- 5 fields: minute hour day-of-month month day-of-week
- Each field: * (any), a single number, or comma-separated numbers (e.g., "0,30")
- Examples: "0 * * * *" (top of every hour), "30 9 * * 1-5" (9:30 Mon-Fri)
- Deliberately minimal — covers the "cron-like" requirement without pulling
  in a third-party cron library. Range/range-step syntax could be added later.
"""

from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
from typing import Callable

from validation.output_validator import is_safe_to_deploy
from schema.output_schema import JoinedCaseOutput


class TriggerType(str, Enum):
    """How a build was triggered."""
    SCHEDULED = "scheduled"
    EVENT = "event"
    MANUAL = "manual"


class BuildStatus(str, Enum):
    """The outcome of a build attempt."""
    SUCCESS = "success"                  # build ran, validation passed
    VALIDATION_FAILED = "validation_failed"  # build ran, validation failed
    BUILD_FAILED = "build_failed"        # build function itself raised an exception


class BuildResult(BaseModel):
    """
    The result of a scheduled or triggered build.

    This is a CANDIDATE output — it has been built and validated but NOT
    deployed. Deploying requires a separate explicit call to
    deploy_gate.deploy_pipeline() with approved=True.

    Attributes:
        trigger_type: how this build was triggered
        trigger_name: the schedule name or event name
        started_at: when the build started
        completed_at: when the build finished
        status: outcome (success / validation_failed / build_failed)
        record_count: number of output records built
        error_count: number of build errors
        is_safe: validation result (True if safe to deploy)
        safety_reasons: validation reasons (empty if safe)
        error_message: exception message if the build itself failed
    """
    trigger_type: str
    trigger_name: str
    started_at: str
    completed_at: str
    status: str
    record_count: int = 0
    error_count: int = 0
    is_safe: bool = False
    safety_reasons: list[str] = []
    error_message: str = ""

    def summary(self) -> dict:
        """Human-readable summary for MCP output."""
        return {
            "trigger_type": self.trigger_type,
            "trigger_name": self.trigger_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "record_count": self.record_count,
            "error_count": self.error_count,
            "is_safe": self.is_safe,
            "safety_reasons": self.safety_reasons,
            "error_message": self.error_message,
        }


class ScheduledJob(BaseModel):
    """A cron-like scheduled build job."""
    name: str
    cron_expression: str
    enabled: bool = True
    last_run_at: str | None = None

    def summary(self) -> dict:
        return {
            "name": self.name,
            "cron_expression": self.cron_expression,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
        }


class EventTrigger(BaseModel):
    """An event-triggered build subscription."""
    name: str
    event_name: str
    enabled: bool = True

    def summary(self) -> dict:
        return {
            "name": self.name,
            "event_name": self.event_name,
            "enabled": self.enabled,
        }


def _parse_cron_field(field: str, min_val: int, max_val: int) -> set[int]:
    """
    Parse a single cron field into a set of matching values.

    Supports: * (all), a single number, or comma-separated numbers.
    Raises ValueError on invalid input.
    """
    if field == "*":
        return set(range(min_val, max_val + 1))

    values = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Empty value in cron field '{field}'")
        try:
            num = int(part)
        except ValueError:
            raise ValueError(f"Invalid cron value '{part}' in field '{field}' — must be an integer or *")
        if num < min_val or num > max_val:
            raise ValueError(f"Cron value {num} out of range [{min_val}, {max_val}] in field '{field}'")
        values.add(num)
    return values


def cron_matches(cron_expression: str, dt: datetime) -> bool:
    """
    Check if a cron expression matches a given datetime.

    Args:
        cron_expression: 5-field cron expression (minute hour dom month dow)
        dt: the datetime to check

    Returns True if the expression matches, False otherwise.
    Raises ValueError if the expression is malformed.
    """
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron expression must have 5 fields (minute hour day-of-month month day-of-week), "
            f"got {len(parts)}: '{cron_expression}'"
        )

    minute_field, hour_field, dom_field, month_field, dow_field = parts

    minutes = _parse_cron_field(minute_field, 0, 59)
    hours = _parse_cron_field(hour_field, 0, 23)
    doms = _parse_cron_field(dom_field, 1, 31)
    months = _parse_cron_field(month_field, 1, 12)
    dows = _parse_cron_field(dow_field, 0, 6)  # 0=Sunday ... 6=Saturday

    # Python weekday(): Monday=0 ... Sunday=6
    # Cron: Sunday=0 ... Saturday=6
    # Convert: cron_dow = (python_weekday + 1) % 7
    cron_dow = (dt.weekday() + 1) % 7

    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in doms
        and dt.month in months
        and cron_dow in dows
    )


class BuildScheduler:
    """
    Schedules and triggers pipeline BUILDS (run + validate), never deploys.

    Usage:
        scheduler = BuildScheduler()

        # Register a build function (runs pipeline, returns records + errors)
        scheduler.add_schedule(
            name="hourly_build",
            cron_expression="0 * * * *",
            build_fn=my_run_pipeline_fn,
        )

        scheduler.add_event_trigger(
            name="on_data_arrival",
            event_name="data_arrived",
            build_fn=my_run_pipeline_fn,
        )

        # Check schedules (e.g., called by a timer loop)
        results = scheduler.check_schedules(now=datetime.now(timezone.utc))

        # Fire an event trigger
        result = scheduler.trigger_event("data_arrived")

        # Review the candidate output (NOT deployed)
        latest = scheduler.get_latest_build()

    The scheduler has NO deploy method and NO reference to deploy_gate.py.
    Deploying a build result requires a separate explicit call to
    deploy_gate.deploy_pipeline() with approved=True.
    """

    def __init__(self):
        self._schedules: dict[str, tuple[ScheduledJob, Callable]] = {}
        self._event_triggers: dict[str, dict[str, tuple[EventTrigger, Callable]]] = {}
        self._build_results: list[BuildResult] = []

    def add_schedule(
        self,
        name: str,
        cron_expression: str,
        build_fn: Callable[[], tuple[list[JoinedCaseOutput], list[tuple]]],
    ) -> ScheduledJob:
        """
        Register a cron-like scheduled build.

        Args:
            name: unique name for this schedule
            cron_expression: 5-field cron expression (minute hour dom month dow)
            build_fn: callable that runs the pipeline and returns (output_records, errors)

        Raises ValueError if name already exists or cron expression is invalid.
        """
        if not name or not name.strip():
            raise ValueError("Schedule name must not be empty")
        if name in self._schedules:
            raise ValueError(f"Schedule '{name}' already exists")
        if build_fn is None:
            raise ValueError("build_fn must not be None")

        # Validate the cron expression by parsing it once
        _parse_cron_field(cron_expression.strip().split()[0], 0, 59)  # will raise if malformed
        # Full validation:
        test_dt = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        cron_matches(cron_expression, test_dt)  # raises ValueError if malformed

        job = ScheduledJob(name=name, cron_expression=cron_expression)
        self._schedules[name] = (job, build_fn)
        return job

    def add_event_trigger(
        self,
        name: str,
        event_name: str,
        build_fn: Callable[[], tuple[list[JoinedCaseOutput], list[tuple]]],
    ) -> EventTrigger:
        """
        Register an event-triggered build.

        Args:
            name: unique name for this trigger
            event_name: the event that triggers the build (e.g., "data_arrived")
            build_fn: callable that runs the pipeline and returns (output_records, errors)

        Raises ValueError if name already exists.
        """
        if not name or not name.strip():
            raise ValueError("Trigger name must not be empty")
        if not event_name or not event_name.strip():
            raise ValueError("event_name must not be empty")
        if build_fn is None:
            raise ValueError("build_fn must not be None")

        # Check name uniqueness across all triggers
        for triggers in self._event_triggers.values():
            if name in triggers:
                raise ValueError(f"Trigger '{name}' already exists")

        trigger = EventTrigger(name=name, event_name=event_name)
        self._event_triggers.setdefault(event_name, {})[name] = (trigger, build_fn)
        return trigger

    def remove_schedule(self, name: str) -> None:
        """Remove a scheduled build. Raises KeyError if not found."""
        if name not in self._schedules:
            raise KeyError(f"Schedule '{name}' does not exist. Available: {list(self._schedules.keys())}")
        del self._schedules[name]

    def remove_event_trigger(self, name: str) -> None:
        """Remove an event trigger. Raises KeyError if not found."""
        for event_name, triggers in self._event_triggers.items():
            if name in triggers:
                del triggers[name]
                if not triggers:
                    del self._event_triggers[event_name]
                return
        raise KeyError(f"Trigger '{name}' does not exist.")

    def enable_schedule(self, name: str) -> ScheduledJob:
        """Enable a scheduled build. Raises KeyError if not found."""
        if name not in self._schedules:
            raise KeyError(f"Schedule '{name}' does not exist.")
        job, build_fn = self._schedules[name]
        job.enabled = True
        return job

    def disable_schedule(self, name: str) -> ScheduledJob:
        """Disable a scheduled build. Raises KeyError if not found."""
        if name not in self._schedules:
            raise KeyError(f"Schedule '{name}' does not exist.")
        job, build_fn = self._schedules[name]
        job.enabled = False
        return job

    def list_schedules(self) -> list[dict]:
        """List all scheduled builds."""
        return [job.summary() for job, _ in self._schedules.values()]

    def list_event_triggers(self) -> list[dict]:
        """List all event triggers."""
        result = []
        for triggers in self._event_triggers.values():
            for trigger, _ in triggers.values():
                result.append(trigger.summary())
        return result

    def check_schedules(self, now: datetime | None = None) -> list[BuildResult]:
        """
        Check all scheduled builds and run any that are due at the given time.

        Args:
            now: the datetime to check against (defaults to current UTC time)

        Returns a list of BuildResults for any builds that were triggered.
        Builds that are disabled or not due are skipped.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        results = []
        for name, (job, build_fn) in self._schedules.items():
            if not job.enabled:
                continue
            if cron_matches(job.cron_expression, now):
                result = self._run_build(build_fn, TriggerType.SCHEDULED, name)
                job.last_run_at = now.isoformat()
                results.append(result)

        return results

    def trigger_event(self, event_name: str) -> list[BuildResult]:
        """
        Fire an event, triggering all builds subscribed to that event.

        Args:
            event_name: the event that occurred (e.g., "data_arrived")

        Returns a list of BuildResults for all builds that were triggered.
        If no triggers are subscribed to this event, returns an empty list.
        """
        if event_name not in self._event_triggers:
            return []

        results = []
        for name, (trigger, build_fn) in self._event_triggers[event_name].items():
            if not trigger.enabled:
                continue
            result = self._run_build(build_fn, TriggerType.EVENT, name)
            results.append(result)

        return results

    def trigger_manual(self, name: str) -> BuildResult:
        """
        Manually trigger a specific scheduled build by name, regardless of
        whether its cron schedule is due. This is for ad-hoc/test runs.

        Raises KeyError if the schedule doesn't exist.
        """
        if name not in self._schedules:
            raise KeyError(f"Schedule '{name}' does not exist. Available: {list(self._schedules.keys())}")
        job, build_fn = self._schedules[name]
        now = datetime.now(timezone.utc)
        result = self._run_build(build_fn, TriggerType.MANUAL, name)
        job.last_run_at = now.isoformat()
        return result

    def _run_build(
        self,
        build_fn: Callable[[], tuple[list[JoinedCaseOutput], list[tuple]]],
        trigger_type: TriggerType,
        trigger_name: str,
    ) -> BuildResult:
        """
        Execute a build: call build_fn, then validate the result.

        This method NEVER calls deploy_gate.py. It only runs the build and
        validates the output, storing the result as a candidate for later
        explicit deployment.
        """
        started_at = datetime.now(timezone.utc).isoformat()

        try:
            output_records, errors = build_fn()
        except Exception as e:
            completed_at = datetime.now(timezone.utc).isoformat()
            result = BuildResult(
                trigger_type=trigger_type.value,
                trigger_name=trigger_name,
                started_at=started_at,
                completed_at=completed_at,
                status=BuildStatus.BUILD_FAILED.value,
                error_message=str(e),
            )
            self._build_results.append(result)
            return result

        # Validate the build result (does NOT deploy)
        is_safe, safety_reasons = is_safe_to_deploy(output_records, errors)

        completed_at = datetime.now(timezone.utc).isoformat()
        status = BuildStatus.SUCCESS.value if is_safe else BuildStatus.VALIDATION_FAILED.value

        result = BuildResult(
            trigger_type=trigger_type.value,
            trigger_name=trigger_name,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            record_count=len(output_records),
            error_count=len(errors),
            is_safe=is_safe,
            safety_reasons=safety_reasons,
        )
        self._build_results.append(result)
        return result

    def get_build_results(self) -> list[BuildResult]:
        """Return all build results (candidate outputs, NOT deployed)."""
        return list(self._build_results)

    def get_latest_build(self) -> BuildResult | None:
        """Return the most recent build result, or None if no builds have run."""
        if not self._build_results:
            return None
        return self._build_results[-1]

    def summary(self) -> dict:
        """Return a summary of the scheduler state."""
        return {
            "schedule_count": len(self._schedules),
            "event_trigger_count": sum(len(t) for t in self._event_triggers.values()),
            "total_builds_run": len(self._build_results),
            "schedules": self.list_schedules(),
            "event_triggers": self.list_event_triggers(),
        }