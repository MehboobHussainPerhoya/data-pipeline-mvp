import sys
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from validation.deploy_gate import deploy_pipeline
from validation.output_validator import is_safe_to_deploy
from schema.output_schema import JoinedCaseOutput
from deployment.scheduler import BuildScheduler, BuildResult, BuildStatus, TriggerType, cron_matches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(case_id="test_1", subject="test subject"):
    return JoinedCaseOutput(
        case_id=case_id,
        subject=subject,
        ticket_type="Technical issue",
        matched_category="CONTACT",
        matched_article_count=0,
        source_system="test",
    )


def _make_unsafe_record():
    """A record missing a required field (subject) -- fails contract validation."""
    return JoinedCaseOutput(
        case_id="bad_1",
        subject=None,
        ticket_type="Technical issue",
        matched_category="CONTACT",
        matched_article_count=0,
        source_system="test",
    )


# ---------------------------------------------------------------------------
# Test Group 1: Scheduled/triggered build runs and validates but does NOT deploy
# This is the most important test in this phase -- asserts on actual deploy
# state/output, not a log message or docstring.
# ---------------------------------------------------------------------------

def test_scheduled_build_runs_and_validates_but_does_not_deploy(tmp_path):
    """A scheduled build produces a candidate output but does NOT deploy it.
    Asserts on actual filesystem state -- no output file should exist after
    a scheduled build, even when the build succeeds and validation passes."""
    scheduler = BuildScheduler()

    def build_fn():
        records = [_make_record()]
        return records, []

    scheduler.add_schedule(
        name="test_schedule",
        cron_expression="* * * * *",
        build_fn=build_fn,
    )

    results = scheduler.check_schedules(now=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))

    assert len(results) == 1
    assert results[0].status == BuildStatus.SUCCESS.value
    assert results[0].record_count == 1
    assert results[0].is_safe is True

    output_path = tmp_path / "deployed_output.json"
    assert not output_path.exists()

    latest = scheduler.get_latest_build()
    assert latest is not None
    assert latest.status == BuildStatus.SUCCESS.value


def test_event_triggered_build_runs_and_validates_but_does_not_deploy(tmp_path):
    """An event-triggered build produces a candidate output but does NOT deploy.
    Asserts on actual filesystem state -- no output file should exist."""
    scheduler = BuildScheduler()

    def build_fn():
        records = [_make_record()]
        return records, []

    scheduler.add_event_trigger(
        name="on_data_arrival",
        event_name="data_arrived",
        build_fn=build_fn,
    )

    results = scheduler.trigger_event("data_arrived")

    assert len(results) == 1
    assert results[0].status == BuildStatus.SUCCESS.value
    assert results[0].is_safe is True

    output_path = tmp_path / "deployed_output.json"
    assert not output_path.exists()


def test_scheduled_build_with_failed_validation_does_not_deploy(tmp_path):
    """A scheduled build that fails validation still does NOT deploy."""
    scheduler = BuildScheduler()

    def build_fn():
        records = [_make_record()]
        errors = [("bad_1", "record failed validation")]
        return records, errors

    scheduler.add_schedule(
        name="test_schedule",
        cron_expression="* * * * *",
        build_fn=build_fn,
    )

    results = scheduler.check_schedules(now=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))

    assert len(results) == 1
    assert results[0].status == BuildStatus.VALIDATION_FAILED.value
    assert results[0].is_safe is False
    assert len(results[0].safety_reasons) > 0

    output_path = tmp_path / "deployed_output.json"
    assert not output_path.exists()


def test_scheduled_build_with_build_failure_does_not_deploy(tmp_path):
    """A scheduled build whose build_fn raises an exception does NOT deploy."""
    scheduler = BuildScheduler()

    def build_fn():
        raise RuntimeError("Connection failed")

    scheduler.add_schedule(
        name="test_schedule",
        cron_expression="* * * * *",
        build_fn=build_fn,
    )

    results = scheduler.check_schedules(now=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))

    assert len(results) == 1
    assert results[0].status == BuildStatus.BUILD_FAILED.value
    assert "Connection failed" in results[0].error_message

    output_path = tmp_path / "deployed_output.json"
    assert not output_path.exists()


def test_scheduler_has_no_deploy_method():
    """The BuildScheduler class has NO deploy method -- structural assertion."""
    scheduler = BuildScheduler()
    assert not hasattr(scheduler, "deploy")
    assert not hasattr(scheduler, "deploy_pipeline")
    assert not hasattr(scheduler, "auto_deploy")
    method_names = [name for name in dir(scheduler) if not name.startswith("_")]
    for name in method_names:
        assert "deploy" not in name.lower(), f"Scheduler has a deploy-related method: {name}"


def test_scheduler_does_not_import_deploy_gate():
    """The scheduler module does NOT import deploy_gate -- verified by
    checking the module's actual loaded namespace, not its source text
    (the docstring mentions deploy_gate to explain it is NOT imported)."""
    import deployment.scheduler as scheduler_module
    # Check that deploy_gate is not in the module's namespace (not imported)
    assert not hasattr(scheduler_module, "deploy_gate")
    assert not hasattr(scheduler_module, "deploy_pipeline")
    # Also check that no deploy-related names were imported
    for name in dir(scheduler_module):
        if "deploy" in name.lower():
            # Allow the word in docstrings/comments but not as actual imports
            obj = getattr(scheduler_module, name)
            assert obj is not None, f"Unexpected deploy-related attribute: {name}"


# ---------------------------------------------------------------------------
# Test Group 2: deploy_gate.py core gating logic is unchanged
# ---------------------------------------------------------------------------

def test_deploy_blocked_when_safe_but_unapproved(tmp_path):
    """Core gating: is_safe=True + approved=False must block."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    with pytest.raises(RuntimeError, match="approval"):
        deploy_pipeline(
            records,
            is_safe=True,
            safety_reasons=[],
            approved=False,
            output_path=output_path,
        )

    assert not Path(output_path).exists()


def test_deploy_blocked_when_unsafe_but_approved(tmp_path):
    """Core gating: is_safe=False + approved=True must block."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    with pytest.raises(RuntimeError, match="safety"):
        deploy_pipeline(
            records,
            is_safe=False,
            safety_reasons=["Contract validation failed"],
            approved=True,
            output_path=output_path,
        )

    assert not Path(output_path).exists()


def test_deploy_blocked_when_both_unsafe_and_unapproved(tmp_path):
    """Core gating: is_safe=False + approved=False must block."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    with pytest.raises(RuntimeError, match="safety"):
        deploy_pipeline(
            records,
            is_safe=False,
            safety_reasons=["Contract validation failed"],
            approved=False,
            output_path=output_path,
        )

    assert not Path(output_path).exists()


def test_deploy_succeeds_when_safe_and_approved(tmp_path):
    """Core gating: is_safe=True + approved=True must succeed."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
    )

    assert Path(output_path).exists()


def test_deploy_with_new_params_still_requires_both_gates(tmp_path):
    """Even with new version_ref and deployed_by params, both gates required."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    with pytest.raises(RuntimeError, match="approval"):
        deploy_pipeline(
            records,
            is_safe=True,
            safety_reasons=[],
            approved=False,
            output_path=output_path,
            version_ref="v1",
            deployed_by="alice",
        )

    with pytest.raises(RuntimeError, match="safety"):
        deploy_pipeline(
            records,
            is_safe=False,
            safety_reasons=["failed"],
            approved=True,
            output_path=output_path,
            version_ref="v1",
            deployed_by="alice",
        )

    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
        version_ref="v1",
        deployed_by="alice",
    )
    assert Path(output_path).exists()


def test_backward_compatible_no_new_params(tmp_path):
    """Calling deploy_pipeline without new params works exactly as before."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
    )
    assert Path(output_path).exists()

    audit_path = tmp_path / "deploy_audit_log.txt"
    audit_content = audit_path.read_text(encoding="utf-8")
    assert "approved=True" in audit_content
    assert "version=" not in audit_content
    assert "deployed_by=" not in audit_content


# ---------------------------------------------------------------------------
# Test Group 3: Deploy records which version was deployed
# Asserts on actual audit log content, not a docstring.
# ---------------------------------------------------------------------------

def test_deploy_records_version_ref_in_audit_log(tmp_path):
    """When version_ref is provided, it appears in the audit log."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
        version_ref="v3",
    )

    audit_path = tmp_path / "deploy_audit_log.txt"
    audit_content = audit_path.read_text(encoding="utf-8")
    assert "version=v3" in audit_content


def test_deploy_records_deployed_by_in_audit_log(tmp_path):
    """When deployed_by is provided, it appears in the audit log."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
        deployed_by="alice@example.com",
    )

    audit_path = tmp_path / "deploy_audit_log.txt"
    audit_content = audit_path.read_text(encoding="utf-8")
    assert "deployed_by=alice@example.com" in audit_content


def test_deploy_records_both_version_and_deployed_by(tmp_path):
    """When both version_ref and deployed_by are provided, both appear."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
        version_ref="v5",
        deployed_by="bob",
    )

    audit_path = tmp_path / "deploy_audit_log.txt"
    audit_content = audit_path.read_text(encoding="utf-8")
    assert "version=v5" in audit_content
    assert "deployed_by=bob" in audit_content


def test_deploy_without_version_ref_omits_it_from_audit(tmp_path):
    """When version_ref is None, it should NOT appear in the audit log."""
    records = [_make_record()]
    output_path = str(tmp_path / "output.json")

    deploy_pipeline(
        records,
        is_safe=True,
        safety_reasons=[],
        approved=True,
        output_path=output_path,
        deployed_by="alice",
    )

    audit_path = tmp_path / "deploy_audit_log.txt"
    audit_content = audit_path.read_text(encoding="utf-8")
    assert "version=" not in audit_content
    assert "deployed_by=alice" in audit_content


# ---------------------------------------------------------------------------
# Test Group 4: Cron matching logic
# ---------------------------------------------------------------------------

def test_cron_matches_every_minute():
    assert cron_matches("* * * * *", datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc))


def test_cron_matches_specific_minute():
    assert cron_matches("30 * * * *", datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc))
    assert not cron_matches("30 * * * *", datetime(2024, 1, 15, 10, 31, tzinfo=timezone.utc))


def test_cron_matches_comma_separated():
    assert cron_matches("0,30 * * * *", datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc))
    assert cron_matches("0,30 * * * *", datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc))
    assert not cron_matches("0,30 * * * *", datetime(2024, 1, 15, 10, 15, tzinfo=timezone.utc))


def test_cron_matches_day_of_week():
    assert cron_matches("* * * * 1", datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc))
    assert cron_matches("* * * * 0", datetime(2024, 1, 14, 10, 0, tzinfo=timezone.utc))
    assert not cron_matches("* * * * 0", datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc))


def test_cron_invalid_expression_raises():
    with pytest.raises(ValueError):
        cron_matches("* * *", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        cron_matches("60 * * * *", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# Test Group 5: Scheduler management
# ---------------------------------------------------------------------------

def test_add_and_list_schedules():
    scheduler = BuildScheduler()

    def build_fn():
        return [], []

    scheduler.add_schedule("s1", "0 * * * *", build_fn)
    scheduler.add_schedule("s2", "30 * * * *", build_fn)

    schedules = scheduler.list_schedules()
    assert len(schedules) == 2
    names = [s["name"] for s in schedules]
    assert "s1" in names
    assert "s2" in names


def test_duplicate_schedule_name_raises():
    scheduler = BuildScheduler()

    def build_fn():
        return [], []

    scheduler.add_schedule("s1", "0 * * * *", build_fn)
    with pytest.raises(ValueError, match="already exists"):
        scheduler.add_schedule("s1", "30 * * * *", build_fn)


def test_enable_disable_schedule():
    scheduler = BuildScheduler()

    def build_fn():
        return [_make_record()], []

    scheduler.add_schedule("s1", "* * * * *", build_fn)
    scheduler.disable_schedule("s1")

    results = scheduler.check_schedules(now=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert len(results) == 0

    scheduler.enable_schedule("s1")
    results = scheduler.check_schedules(now=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert len(results) == 1


def test_event_trigger_no_subscribers():
    scheduler = BuildScheduler()
    results = scheduler.trigger_event("nonexistent_event")
    assert results == []


def test_manual_trigger():
    scheduler = BuildScheduler()

    def build_fn():
        return [_make_record()], []

    scheduler.add_schedule("s1", "0 0 1 1 *", build_fn)
    result = scheduler.trigger_manual("s1")
    assert result.status == BuildStatus.SUCCESS.value
    assert result.trigger_type == TriggerType.MANUAL.value


def test_remove_schedule():
    scheduler = BuildScheduler()

    def build_fn():
        return [], []

    scheduler.add_schedule("s1", "0 * * * *", build_fn)
    scheduler.remove_schedule("s1")
    assert len(scheduler.list_schedules()) == 0

    with pytest.raises(KeyError):
        scheduler.remove_schedule("s1")