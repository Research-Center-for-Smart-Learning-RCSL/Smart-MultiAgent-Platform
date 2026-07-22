"""Regression coverage for terminal workflow A2A cancellation retries."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks.workflow_steps import workflow_cancel_a2a_calls


@pytest.mark.asyncio
async def test_a2a_cancellation_failure_is_reenqueued() -> None:
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    redis = MagicMock(enqueue_job=AsyncMock())
    run_id = str(uuid.uuid4())

    with (
        patch("shared_kernel.db.session.async_session", return_value=session_cm),
        patch(
            "contexts.orchestration.interfaces.facade.OrchestrationFacade.cancel_workflow_run_calls",
            new=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        ),
    ):
        result = await workflow_cancel_a2a_calls({"redis": redis}, run_id, attempt=3)

    assert result == "retrying"
    redis.enqueue_job.assert_awaited_once()
    args = redis.enqueue_job.await_args.args
    assert args == ("workflow_cancel_a2a_calls", run_id, 4)
    assert redis.enqueue_job.await_args.kwargs["_defer_by"].total_seconds() == 8
