"""
Phase 10 — Monitoring & Alerting tests (FSD 4.15).

Covers:
- FR-MON-01: Run health dashboard correctly reflects a real success and a
  real failure from the scheduler's BuildResult history.
- FR-MON-02: Data-quality metrics correctly detect null-rate and duplicates
  on realistic bad data; schema-drift correctly detects when output no longer
  matches the registered schema.
- FR-MON-03: An SLA breach (run exceeding a threshold) correctly generates an
  alert record; a run failure correctly generates an alert record; no alert
  is generated for a normal successful run within SLA.

All tests assert on actual behavior/state, not docstrings or log messages.
"""

import sys
import json
from pathlib import Path
import pytest
from datetime import datetime, timezone, timedelta

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from deployment.scheduler import BuildScheduler, BuildResult, BuildStatus, TriggerType
from schema.output_schema import JoinedCaseOutput
from monitoring.run_health import RunHealthDashboard
from monitoring.data_quality import DataQualityAnalyzer
from monitoring.alerting import (
    AlertEngine, AlertRecord, AlertSeverity, AlertType,
    SLAConfig, StubNotificationChannel,
)


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


def _make_build_result(
    trigger_name="test_pipeline",
    status=BuildStatus.SUCCESS.value,
    started_at=None,
    completed_at=None,
    record_count=100,
    error_count=0,
    is_safe=True,
    safety_reasons=None,
    error_message="",
):
    """Create a BuildResult with explicit timestamps for deterministic tests."""
    if started_at is None:
        started_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    if completed_at is None:
        completed_at = started_at + timedelta(seconds=30)
    if safety_reasons is None:
        safety_reasons = []
    return BuildResult(
        trigger_type=TriggerType.SCHEDULED.value,
        trigger_name=trigger_name,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        status=status,
        record_count=record_count,
        error_count=error_count,
        is_safe=is_safe,
        safety_reasons=safety_reasons,
        error_message=error_message,
    )


class FakeScheduler:
    """
    A minimal scheduler stand-in that returns pre-built BuildResults.
    This lets us test the monitoring layer with deterministic data without
    running the real pipeline. It implements only get_build_results(),
    which is the interface RunHealthDashboard and AlertEngine use.
    """
    def __init__(self, results):
        self._results = results

    def get_build_results(self):
        return list(self._results)


# ---------------------------------------------------------------------------
# FR-MON-01: Run Health Dashboard
# ---------------------------------------------------------------------------

class TestRunHealthDashboard:

    def test_dashboard_reflects_successful_run(self):
        """A successful build is reflected with status=success, computed duration,
        and the right record count."""
        result = _make_build_result(
            status=BuildStatus.SUCCESS.value,
            record_count=8669,
            started_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2024, 1, 1, 10, 2, 30, tzinfo=timezone.utc),
        )
        scheduler = FakeScheduler([result])
        dashboard = RunHealthDashboard(scheduler)

        runs = dashboard.get_all_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "success"
        assert run.record_count == 8669
        assert run.duration_seconds == 150.0
        assert run.is_safe is True
        assert run.pipeline_name == "test_pipeline"

    def test_dashboard_reflects_failed_run(self):
        """A failed build (validation_failed) is reflected with failure status."""
        result = _make_build_result(
            status=BuildStatus.VALIDATION_FAILED.value,
            is_safe=False,
            safety_reasons=["Contract validation failed"],
            record_count=0,
        )
        scheduler = FakeScheduler([result])
        dashboard = RunHealthDashboard(scheduler)

        runs = dashboard.get_all_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "validation_failed"
        assert run.is_safe is False
        assert run.record_count == 0

    def test_dashboard_reflects_build_failure(self):
        """A build_failed result is reflected with the error status."""
        result = _make_build_result(
            status=BuildStatus.BUILD_FAILED.value,
            error_message="Connection refused",
            record_count=0,
        )
        scheduler = FakeScheduler([result])
        dashboard = RunHealthDashboard(scheduler)

        runs = dashboard.get_all_runs()
        assert runs[0].status == "build_failed"

    def test_trend_computes_success_rate_and_averages(self):
        """Trend computes success rate, avg duration, and avg record count."""
        base = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        results = [
            _make_build_result(
                status=BuildStatus.SUCCESS.value, record_count=100,
                started_at=base, completed_at=base + timedelta(seconds=30),
            ),
            _make_build_result(
                status=BuildStatus.SUCCESS.value, record_count=200,
                started_at=base + timedelta(hours=1),
                completed_at=base + timedelta(hours=1, seconds=50),
            ),
            _make_build_result(
                status=BuildStatus.VALIDATION_FAILED.value, record_count=0,
                started_at=base + timedelta(hours=2),
                completed_at=base + timedelta(hours=2, seconds=10),
                is_safe=False, safety_reasons=["error"],
            ),
        ]
        scheduler = FakeScheduler(results)
        dashboard = RunHealthDashboard(scheduler)

        trend = dashboard.get_trend("test_pipeline")
        assert trend is not None
        assert trend.total_runs == 3
        assert trend.success_count == 2
        assert trend.failure_count == 1
        assert trend.success_rate == pytest.approx(2 / 3, rel=0.01)
        assert trend.avg_duration_seconds == pytest.approx(30.0, abs=0.1)
        assert trend.avg_record_count == pytest.approx(150.0, abs=0.1)
        assert trend.latest_status == "validation_failed"

    def test_row_count_trend_history(self):
        """Row-count history is captured in chronological order."""
        results = [
            _make_build_result(record_count=100),
            _make_build_result(record_count=150),
            _make_build_result(record_count=200),
        ]
        scheduler = FakeScheduler(results)
        dashboard = RunHealthDashboard(scheduler)

        trend = dashboard.get_trend("test_pipeline")
        counts = [rc for _, rc in trend.record_count_history]
        assert counts == [100, 150, 200]

    def test_deploy_history_reads_audit_log(self, tmp_path):
        """Deploy history is parsed from deploy_audit_log.txt."""
        audit_path = tmp_path / "deploy_audit_log.txt"
        audit_path.write_text(
            "2024-01-01T10:00:00 | Deployed 8669 records to /data/output.json | approved=True\n"
            "2024-01-02T10:00:00 | Deployed 9000 records to /data/output.json | approved=True | version=v3 | deployed_by=alice\n",
            encoding="utf-8",
        )
        scheduler = FakeScheduler([])
        dashboard = RunHealthDashboard(scheduler)

        history = dashboard.get_deploy_history(str(audit_path))
        assert len(history) == 2
        assert history[0]["record_count"] == 8669
        assert history[0]["approved"] == "True"
        assert history[1]["record_count"] == 9000
        assert history[1]["version"] == "v3"
        assert history[1]["deployed_by"] == "alice"

    def test_empty_scheduler_returns_empty_runs(self):
        """An empty scheduler history returns no runs."""
        scheduler = FakeScheduler([])
        dashboard = RunHealthDashboard(scheduler)
        assert dashboard.get_all_runs() == []
        assert dashboard.get_latest_run() is None
        assert dashboard.get_all_trends() == []


# ---------------------------------------------------------------------------
# FR-MON-02: Data-Quality Metrics
# ---------------------------------------------------------------------------

class TestDataQualityMetrics:

    def test_null_rate_detected_on_real_data(self):
        """Null-rate is correctly computed when output records have null fields."""
        records = [
            {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": 0, "source_system": "t"},
            {"case_id": "2", "subject": "s2", "ticket_type": None, "matched_category": "C", "matched_article_count": 1, "source_system": "t"},
            {"case_id": "3", "subject": "s3", "ticket_type": "B", "matched_category": None, "matched_article_count": 0, "source_system": "t"},
            {"case_id": "4", "subject": "s4", "ticket_type": None, "matched_category": "P", "matched_article_count": 2, "source_system": "t"},
        ]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        ticket_null = [nr for nr in metrics.null_rates if nr.field_name == "ticket_type"][0]
        assert ticket_null.null_count == 2
        assert ticket_null.null_rate == 0.5

        category_null = [nr for nr in metrics.null_rates if nr.field_name == "matched_category"][0]
        assert category_null.null_count == 1
        assert category_null.null_rate == 0.25

        case_null = [nr for nr in metrics.null_rates if nr.field_name == "case_id"][0]
        assert case_null.null_count == 0
        assert case_null.null_rate == 0.0

    def test_duplicate_rate_detected_on_real_data(self):
        """Duplicate-rate is correctly computed when output has exact-duplicate rows."""
        row1 = {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": 0, "source_system": "t"}
        row2 = {"case_id": "2", "subject": "s2", "ticket_type": "B", "matched_category": "P", "matched_article_count": 1, "source_system": "t"}
        row3 = {"case_id": "3", "subject": "s3", "ticket_type": None, "matched_category": "C", "matched_article_count": 0, "source_system": "t"}
        records = [dict(row1), dict(row2), dict(row1), dict(row3), dict(row2)]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        assert metrics.record_count == 5
        assert metrics.duplicate_count == 2
        assert metrics.unique_record_count == 3
        assert metrics.duplicate_rate == pytest.approx(2 / 5, rel=0.01)

    def test_no_duplicates_on_unique_data(self):
        """Duplicate-rate is 0 when all records are unique."""
        records = [
            {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": 0, "source_system": "t"},
            {"case_id": "2", "subject": "s2", "ticket_type": "B", "matched_category": "P", "matched_article_count": 1, "source_system": "t"},
        ]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        assert metrics.duplicate_count == 0
        assert metrics.duplicate_rate == 0.0
        assert metrics.unique_record_count == 2

    def test_schema_drift_detects_extra_field(self):
        """Schema-drift is detected when output has a field not in the registered schema."""
        records = [
            {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": 0, "source_system": "t", "rogue_field": "x"},
        ]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        assert metrics.has_schema_drift is True
        extra_drifts = [d for d in metrics.schema_drift_fields if d.drift_type == "extra_field"]
        assert len(extra_drifts) == 1
        assert extra_drifts[0].field_name == "rogue_field"

    def test_schema_drift_detects_missing_field(self):
        """Schema-drift is detected when output is missing a registered field."""
        records = [
            {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": 0},
        ]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        assert metrics.has_schema_drift is True
        missing_drifts = [d for d in metrics.schema_drift_fields if d.drift_type == "missing_field"]
        assert len(missing_drifts) == 1
        assert missing_drifts[0].field_name == "source_system"

    def test_schema_drift_detects_type_mismatch(self):
        """Schema-drift is detected when a field's inferred type doesn't match."""
        records = [
            {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": "not_a_number", "source_system": "t"},
            {"case_id": "2", "subject": "s2", "ticket_type": "B", "matched_category": "P", "matched_article_count": "also_not_a_number", "source_system": "t"},
        ]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        assert metrics.has_schema_drift is True
        type_drifts = [d for d in metrics.schema_drift_fields if d.drift_type == "type_mismatch"]
        assert len(type_drifts) >= 1
        mismatch = [d for d in type_drifts if d.field_name == "matched_article_count"][0]
        assert mismatch.expected == "Integer"
        assert mismatch.actual == "String"

    def test_no_schema_drift_on_clean_data(self):
        """No schema drift when output matches the registered schema exactly."""
        records = [
            {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": 0, "source_system": "t"},
            {"case_id": "2", "subject": "s2", "ticket_type": "B", "matched_category": "P", "matched_article_count": 1, "source_system": "t"},
        ]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        assert metrics.has_schema_drift is False
        assert metrics.schema_drift_fields == []

    def test_dq_metrics_with_pydantic_models(self):
        """DQ metrics work with Pydantic model objects, not just dicts."""
        records = [
            _make_record(case_id="1", subject="issue 1"),
            _make_record(case_id="2", subject="issue 2"),
        ]
        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze(records, schema_name="JoinedCaseOutput")

        assert metrics.record_count == 2
        assert metrics.duplicate_count == 0
        subject_null = [nr for nr in metrics.null_rates if nr.field_name == "subject"][0]
        assert subject_null.null_rate == 0.0

    def test_dq_metrics_from_file(self, tmp_path):
        """DQ metrics can be loaded from a JSON output file."""
        records = [
            {"case_id": "1", "subject": "s1", "ticket_type": "T", "matched_category": "C", "matched_article_count": 0, "source_system": "t"},
            {"case_id": "2", "subject": "s2", "ticket_type": None, "matched_category": "P", "matched_article_count": 1, "source_system": "t"},
        ]
        file_path = tmp_path / "output.json"
        file_path.write_text(json.dumps(records), encoding="utf-8")

        analyzer = DataQualityAnalyzer()
        metrics = analyzer.analyze_from_file(str(file_path), schema_name="JoinedCaseOutput")

        assert metrics.record_count == 2
        ticket_null = [nr for nr in metrics.null_rates if nr.field_name == "ticket_type"][0]
        assert ticket_null.null_count == 1
        assert ticket_null.null_rate == 0.5


# ---------------------------------------------------------------------------
# FR-MON-03: SLA/Failure Alerting
# ---------------------------------------------------------------------------

class TestAlerting:

    def test_sla_breach_generates_alert_record(self):
        """An SLA breach (run exceeding a configured duration threshold) correctly
        generates an alert record with the right type, severity, owner, and reason."""
        base = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = _make_build_result(
            status=BuildStatus.SUCCESS.value,
            record_count=8669,
            started_at=base,
            completed_at=base + timedelta(seconds=300),
        )
        scheduler = FakeScheduler([result])

        engine = AlertEngine()
        engine.add_sla(SLAConfig(
            pipeline_name="test_pipeline",
            max_duration_seconds=120,
            owner="alice@example.com",
        ))

        new_alerts = engine.evaluate(scheduler)

        assert len(new_alerts) == 1
        alert = new_alerts[0]
        assert alert.alert_type == AlertType.SLA_BREACH.value
        assert alert.severity == AlertSeverity.WARNING.value
        assert alert.pipeline_name == "test_pipeline"
        assert alert.owner == "alice@example.com"
        assert alert.run_status == "success"
        assert alert.run_duration_seconds == 300.0
        assert "exceeded SLA" in alert.reason
        assert "300" in alert.reason
        assert "120" in alert.reason

    def test_sla_breach_alert_is_retrievable(self):
        """An SLA breach alert is retrievable via the notification channel."""
        base = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = _make_build_result(
            status=BuildStatus.SUCCESS.value,
            started_at=base,
            completed_at=base + timedelta(seconds=600),
        )
        scheduler = FakeScheduler([result])

        channel = StubNotificationChannel()
        engine = AlertEngine(channel=channel)
        engine.add_sla(SLAConfig("test_pipeline", max_duration_seconds=60, owner="bob"))
        engine.evaluate(scheduler)

        alerts = engine.get_alerts()
        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.SLA_BREACH.value
        assert alerts[0].owner == "bob"

# ---------------------------------------------------------------------------
# FR-MON-03: Additional Alerting Tests
# ---------------------------------------------------------------------------

class TestAlertingExtended:

    def test_run_failure_generates_alert_record(self):
        """A run failure (validation_failed) correctly generates a critical alert."""
        result = _make_build_result(
            status=BuildStatus.VALIDATION_FAILED.value,
            is_safe=False,
            safety_reasons=["Contract validation failed"],
            record_count=0,
        )
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        new_alerts = engine.evaluate(scheduler)
        assert len(new_alerts) == 1
        alert = new_alerts[0]
        assert alert.alert_type == AlertType.RUN_FAILURE.value
        assert alert.severity == AlertSeverity.CRITICAL.value
        assert alert.pipeline_name == "test_pipeline"
        assert alert.run_status == "validation_failed"
        assert "failed validation" in alert.reason

    def test_build_failure_generates_alert_record(self):
        """A build_failed status correctly generates a critical alert."""
        result = _make_build_result(
            status=BuildStatus.BUILD_FAILED.value,
            error_message="Connection refused",
            record_count=0,
        )
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        new_alerts = engine.evaluate(scheduler)
        assert len(new_alerts) == 1
        alert = new_alerts[0]
        assert alert.alert_type == AlertType.RUN_FAILURE.value
        assert alert.severity == AlertSeverity.CRITICAL.value
        assert "Connection refused" in alert.reason

    def test_no_alert_for_successful_run_within_sla(self):
        """No alert is generated for a normal successful run within SLA."""
        base = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = _make_build_result(
            status=BuildStatus.SUCCESS.value,
            record_count=8669,
            started_at=base,
            completed_at=base + timedelta(seconds=30),
        )
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        engine.add_sla(SLAConfig("test_pipeline", max_duration_seconds=120, owner="alice"))
        new_alerts = engine.evaluate(scheduler)
        assert len(new_alerts) == 0
        assert engine.get_alerts() == []

    def test_no_alert_for_successful_run_without_sla_config(self):
        """No alert for a successful run when no SLA is configured."""
        result = _make_build_result(status=BuildStatus.SUCCESS.value, record_count=100)
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        new_alerts = engine.evaluate(scheduler)
        assert len(new_alerts) == 0

    def test_failure_and_sla_breach_both_generated(self):
        """A failed run that also would breach SLA generates only the failure alert."""
        base = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = _make_build_result(
            status=BuildStatus.VALIDATION_FAILED.value,
            is_safe=False,
            safety_reasons=["error"],
            started_at=base,
            completed_at=base + timedelta(seconds=600),
        )
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        engine.add_sla(SLAConfig("test_pipeline", max_duration_seconds=60, owner="alice"))
        new_alerts = engine.evaluate(scheduler)
        assert len(new_alerts) == 1
        assert new_alerts[0].alert_type == AlertType.RUN_FAILURE.value

    def test_multiple_runs_generate_multiple_alerts(self):
        """Multiple failed runs each generate their own alert record."""
        results = [
            _make_build_result(trigger_name="pipeline_a", status=BuildStatus.VALIDATION_FAILED.value, is_safe=False, safety_reasons=["error1"]),
            _make_build_result(trigger_name="pipeline_b", status=BuildStatus.BUILD_FAILED.value, error_message="crash"),
        ]
        scheduler = FakeScheduler(results)
        engine = AlertEngine()
        new_alerts = engine.evaluate(scheduler)
        assert len(new_alerts) == 2
        assert new_alerts[0].pipeline_name == "pipeline_a"
        assert new_alerts[1].pipeline_name == "pipeline_b"

    def test_evaluate_is_idempotent(self):
        """Calling evaluate() twice does not duplicate alerts."""
        result = _make_build_result(status=BuildStatus.VALIDATION_FAILED.value, is_safe=False, safety_reasons=["error"])
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        first_alerts = engine.evaluate(scheduler)
        second_alerts = engine.evaluate(scheduler)
        assert len(first_alerts) == 1
        assert len(second_alerts) == 0
        assert len(engine.get_alerts()) == 1

    def test_alert_owner_from_sla_config(self):
        """The alert owner is taken from the SLA config when available."""
        result = _make_build_result(status=BuildStatus.VALIDATION_FAILED.value, is_safe=False, safety_reasons=["error"], trigger_name="my_pipeline")
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        engine.add_sla(SLAConfig("my_pipeline", max_duration_seconds=60, owner="oncall@example.com"))
        alerts = engine.evaluate(scheduler)
        assert alerts[0].owner == "oncall@example.com"

    def test_alert_owner_default_without_sla_config(self):
        """The alert owner defaults to pipeline-owner when no SLA config exists."""
        result = _make_build_result(status=BuildStatus.VALIDATION_FAILED.value, is_safe=False, safety_reasons=["error"])
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        alerts = engine.evaluate(scheduler)
        assert alerts[0].owner == "pipeline-owner"

    def test_stub_channel_persists_to_file(self, tmp_path):
        """The stub notification channel writes alerts to a JSON file."""
        result = _make_build_result(status=BuildStatus.BUILD_FAILED.value, error_message="crash")
        scheduler = FakeScheduler([result])
        file_path = str(tmp_path / "alerts.json")
        channel = StubNotificationChannel(file_path=file_path)
        engine = AlertEngine(channel=channel)
        engine.evaluate(scheduler)
        alerts_file = Path(file_path)
        assert alerts_file.exists()
        saved = json.loads(alerts_file.read_text(encoding="utf-8"))
        assert len(saved) == 1
        assert saved[0]["alert_type"] == AlertType.RUN_FAILURE.value

    def test_alert_record_has_all_required_fields(self):
        """An alert record has all required fields."""
        result = _make_build_result(status=BuildStatus.VALIDATION_FAILED.value, is_safe=False, safety_reasons=["error"])
        scheduler = FakeScheduler([result])
        engine = AlertEngine()
        alerts = engine.evaluate(scheduler)
        alert = alerts[0]
        assert alert.alert_id is not None and len(alert.alert_id) > 0
        assert alert.alert_type is not None
        assert alert.severity is not None
        assert alert.pipeline_name is not None
        assert alert.run_id is not None
        assert alert.owner is not None
        assert alert.reason is not None and len(alert.reason) > 0
        assert alert.timestamp is not None
        assert alert.run_started_at is not None
        assert alert.run_status is not None

    def test_alerts_filterable_by_pipeline(self):
        """Alerts can be filtered by pipeline name."""
        results = [
            _make_build_result(trigger_name="p1", status=BuildStatus.BUILD_FAILED.value, error_message="e1"),
            _make_build_result(trigger_name="p2", status=BuildStatus.BUILD_FAILED.value, error_message="e2"),
        ]
        scheduler = FakeScheduler(results)
        engine = AlertEngine()
        engine.evaluate(scheduler)
        p1_alerts = engine.get_alerts_for_pipeline("p1")
        assert len(p1_alerts) == 1
        assert p1_alerts[0].pipeline_name == "p1"

    def test_alert_summary(self):
        """The alert engine summary correctly counts alerts by type and severity."""
        results = [
            _make_build_result(trigger_name="p1", status=BuildStatus.BUILD_FAILED.value, error_message="e1"),
            _make_build_result(trigger_name="p2", status=BuildStatus.VALIDATION_FAILED.value, is_safe=False, safety_reasons=["e2"]),
        ]
        scheduler = FakeScheduler(results)
        engine = AlertEngine()
        engine.evaluate(scheduler)
        summary = engine.summary()
        assert summary["total_alerts"] == 2
        assert summary["run_failure_count"] == 2
        assert summary["critical_count"] == 2
        assert summary["sla_breach_count"] == 0
