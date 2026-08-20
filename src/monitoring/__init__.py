"""
Monitoring package — run health, data-quality metrics, and alerting.

FSD requirements: FR-MON-01, FR-MON-02, FR-MON-03 (Section 4.15)

This package is OBSERVATIONAL ONLY — it reads and reports on what already
happened (builds, deploys, output quality). It does not change how the
pipeline runs, joins, validates, or deploys.
"""

from .run_health import RunHealthDashboard, RunSummary, RunTrend
from .data_quality import DataQualityAnalyzer, DQMetrics
from .alerting import (
    AlertEngine, AlertRecord, AlertSeverity, AlertType,
    SLAConfig, StubNotificationChannel,
)

__all__ = [
    "RunHealthDashboard", "RunSummary", "RunTrend",
    "DataQualityAnalyzer", "DQMetrics",
    "AlertEngine", "AlertRecord", "AlertSeverity", "AlertType",
    "SLAConfig", "StubNotificationChannel",
]
