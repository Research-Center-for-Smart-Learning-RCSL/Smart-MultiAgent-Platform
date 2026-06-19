"""Step lifecycle recorder — insert, update, observe, and publish (H.4).

Extracted from :class:`RunEngine._execute_node` to separate the step
persistence/eventing concern from the node-execution orchestration logic.
Internal to the workflow context — no cross-context surface.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.domain.models import StepOutcome, StepState
from contexts.workflow.infrastructure.channels import workflow_channel
from contexts.workflow.infrastructure.repositories import WorkflowStepRepository
from shared_kernel import audit
from shared_kernel.observability.metrics import WORKFLOW_STEP_DURATION_SECONDS, WORKFLOW_STEPS_TOTAL
from shared_kernel.realtime.pubsub import Publisher


class StepRecorder:
    """Manages the lifecycle of a single workflow step row and its events."""

    def __init__(self, db: AsyncSession, steps: WorkflowStepRepository) -> None:
        self._db = db
        self._steps = steps

    async def insert_step(
        self,
        *,
        run_id: uuid.UUID,
        node_id: str,
        input_data: dict[str, Any],
    ) -> Any:
        """Insert a RUNNING step row and return the step object."""
        return await self._steps.insert(
            run_id=run_id,
            node_id=node_id,
            state=StepState.RUNNING,
            input_data=input_data,
        )

    async def emit_step_started(
        self,
        *,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        node_id: str,
        node_type: str,
    ) -> None:
        """Publish ``workflow.step_started`` WS event and audit row."""
        pub = Publisher(workflow_channel(run_id))
        await pub.emit(
            "workflow.step_started",
            {
                "step_id": str(step_id),
                "node_id": node_id,
                "node_type": node_type,
            },
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.step_started",
                resource_type="workflow_step",
                resource_id=step_id,
                metadata={"run_id": str(run_id), "node_id": node_id},
            ),
        )

    async def update_step(
        self,
        step_id: uuid.UUID,
        *,
        outcome: StepOutcome,
        node_type: str,
        step_start: float,
    ) -> None:
        """Update the step row with outcome, observe metrics, and flush.

        ``step_start`` is a :func:`time.monotonic` timestamp captured before
        the executor ran; the elapsed wall-clock is used for the Prometheus
        histogram observation.
        """
        now = datetime.now(UTC)
        await self._steps.update(
            step_id,
            state=outcome.state,
            ended_at=now if outcome.state not in (StepState.RUNNING, StepState.PENDING) else None,
            output=outcome.output,
            error=outcome.error,
        )
        elapsed = time.monotonic() - step_start
        WORKFLOW_STEP_DURATION_SECONDS.labels(node_type=node_type).observe(elapsed)
        if outcome.state not in (StepState.RUNNING, StepState.PENDING):
            WORKFLOW_STEPS_TOTAL.labels(
                node_type=node_type,
                state=outcome.state.value,
            ).inc()
        # W12: flush step outcome durably before following edges so that if
        # _advance_from raises, the step result is preserved in the eventual commit.
        await self._db.flush()

    async def emit_step_event(
        self,
        *,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        node_id: str,
        outcome: StepOutcome,
    ) -> None:
        """Publish ``workflow.step_finished`` or ``workflow.step_failed`` WS event."""
        action = (
            "workflow.step_failed" if outcome.state == StepState.FAILED else "workflow.step_finished"
        )
        pub = Publisher(workflow_channel(run_id))
        await pub.emit(
            action,
            {
                "step_id": str(step_id),
                "node_id": node_id,
                "state": outcome.state.value,
            },
        )


__all__ = ["StepRecorder"]
