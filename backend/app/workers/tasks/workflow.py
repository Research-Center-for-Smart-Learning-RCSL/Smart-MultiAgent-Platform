"""Arq tasks for the workflow engine (H.4).

Backward-compatibility re-export module. The actual implementations now live in
focused sub-modules:

- workflow_steps.py     — step execution (run_workflow_step, retry_workflow_node, etc.)
- workflow_cron.py      — cron scheduling (workflow_cron_scheduler)
- workflow_signals.py   — signal dispatch and event resume
- workflow_approvals.py — approval / instruct gate resume + timeout
- workflow_watchdog.py  — timeout watchdog sweep

Nightly 90-day run archival now lives in ``retention.retention_sweep``
(see its ``_archive_workflow_runs`` policy) — a single retention path per
table replaces the former duplicate ``archive_workflow_runs`` cron.
"""

from __future__ import annotations

# Re-export everything so existing ``from app.workers.tasks.workflow import X``
# and ``from app.workers.tasks import workflow as wf; wf.X`` keep working.
from app.workers.tasks.workflow_approvals import (
    _approval_port,
    _store_instruct_output,
    workflow_instruct_timeout,
    workflow_resume_approval,
    workflow_resume_instruct,
)
from app.workers.tasks.workflow_common import (
    _CLAIM_RESTORE_TTL_S,
    _RESUME_RETRY_DELAY_S,
    _RESUME_RETRY_MAX_ATTEMPTS,
    _emit_resumed,
    _restore_claim,
    _run_is_terminal,
)
from app.workers.tasks.workflow_cron import (
    workflow_cron_scheduler,
)
from app.workers.tasks.workflow_signals import (
    run_triggered_workflow,
    workflow_event_resume,
    workflow_event_timeout,
    workflow_signal,
    workflow_variable_signal,
)
from app.workers.tasks.workflow_steps import (
    retry_workflow_node,
    run_workflow_step,
    workflow_subagent_complete,
    workflow_subagent_timeout,
)
from app.workers.tasks.workflow_watchdog import (
    workflow_watchdog,
)

__all__ = [
    # Steps
    "run_workflow_step",
    "retry_workflow_node",
    "workflow_subagent_timeout",
    "workflow_subagent_complete",
    # Cron
    "workflow_cron_scheduler",
    # Signals
    "workflow_signal",
    "workflow_variable_signal",
    "workflow_event_resume",
    "run_triggered_workflow",
    "workflow_event_timeout",
    # Approvals
    "workflow_resume_approval",
    "workflow_resume_instruct",
    "workflow_instruct_timeout",
    "_approval_port",
    "_store_instruct_output",
    # Watchdog
    "workflow_watchdog",
    # Common helpers (re-exported for backward compat)
    "_run_is_terminal",
    "_restore_claim",
    "_emit_resumed",
    "_RESUME_RETRY_DELAY_S",
    "_RESUME_RETRY_MAX_ATTEMPTS",
    "_CLAIM_RESTORE_TTL_S",
]
