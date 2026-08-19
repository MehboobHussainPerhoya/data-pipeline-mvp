"""
Deployment package — Deployment Module (FSD 4.14).

Phase 9: Scheduled & triggered builds (FR-DEPLOY-02).

The scheduler automates BUILD execution (run + validate) on a cron-like
schedule or in response to an event. It NEVER deploys on its own — a
scheduled/triggered build produces a candidate output that must still pass
through deploy_gate.py's approval gate (is_safe=True AND approved=True)
before anything is actually deployed. Scheduling automates the build,
never the approval.
"""

from .scheduler import BuildScheduler, TriggerType, BuildResult

__all__ = [
    "BuildScheduler",
    "TriggerType",
    "BuildResult",
]