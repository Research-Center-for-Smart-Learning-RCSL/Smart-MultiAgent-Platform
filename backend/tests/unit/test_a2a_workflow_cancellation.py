"""Workflow termination must cancel its live synchronous A2A calls."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.orchestration.application.a2a_service import A2AService
from contexts.orchestration.domain.errors import A2ACallCancelled
from contexts.orchestration.infrastructure import a2a_rendezvous


@pytest.mark.asyncio
async def test_call_returns_cancelled_when_its_run_terminates(monkeypatch) -> None:
    service = A2AService(MagicMock())
    run_id = uuid.uuid4()
    register = AsyncMock(return_value=False)
    unregister = AsyncMock()
    send = AsyncMock()

    monkeypatch.setattr(a2a_rendezvous, "register_expected_responder", AsyncMock())
    monkeypatch.setattr(a2a_rendezvous, "register_workflow_call", register)
    monkeypatch.setattr(a2a_rendezvous, "unregister_workflow_call", unregister)
    monkeypatch.setattr(
        a2a_rendezvous,
        "await_reply",
        AsyncMock(return_value={"payload": {a2a_rendezvous.A2A_CANCELLED_KEY: True}}),
    )
    monkeypatch.setattr(service, "send", send)

    with pytest.raises(A2ACallCancelled):
        await service.call(
            from_agent_id=None,
            to_agent_id=uuid.uuid4(),
            payload={"input": "x"},
            workflow_run_id=run_id,
        )

    register.assert_awaited_once()
    send.assert_awaited_once()
    unregister.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_does_not_dispatch_after_terminal_run_was_observed(monkeypatch) -> None:
    service = A2AService(MagicMock())
    run_id = uuid.uuid4()
    unregister = AsyncMock()
    send = AsyncMock()

    monkeypatch.setattr(a2a_rendezvous, "register_expected_responder", AsyncMock())
    monkeypatch.setattr(a2a_rendezvous, "register_workflow_call", AsyncMock(return_value=True))
    monkeypatch.setattr(a2a_rendezvous, "unregister_workflow_call", unregister)
    monkeypatch.setattr(service, "send", send)

    with pytest.raises(A2ACallCancelled):
        await service.call(
            from_agent_id=None,
            to_agent_id=uuid.uuid4(),
            payload={"input": "x"},
            workflow_run_id=run_id,
        )

    send.assert_not_awaited()
    unregister.assert_awaited_once()
