"""Workflow-originated A2A must respect the tenant boundary (from_agent=None)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.orchestration.application.a2a_service import A2AService
from contexts.orchestration.domain.errors import A2AForbidden


def _svc() -> A2AService:
    return A2AService(db=AsyncMock())


def _callee(project_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=project_id)


@pytest.mark.asyncio
async def test_missing_run_id_is_denied() -> None:
    svc = _svc()
    with pytest.raises(A2AForbidden):
        await svc._enforce_workflow_tenant(None, _callee(uuid.uuid4()))


@pytest.mark.asyncio
async def test_cross_project_run_is_denied() -> None:
    svc = _svc()
    run_id = uuid.uuid4()
    callee = _callee(uuid.uuid4())
    other_project = uuid.uuid4()
    facade = AsyncMock()
    facade.get_run_project_id.return_value = other_project
    with (
        patch("contexts.workflow.interfaces.facade.WorkflowFacade", return_value=facade),
        patch("contexts.orchestration.application.a2a_service.audit.emit", new=AsyncMock()),
        pytest.raises(A2AForbidden),
    ):
        await svc._enforce_workflow_tenant(run_id, callee)


@pytest.mark.asyncio
async def test_same_project_run_is_allowed() -> None:
    svc = _svc()
    run_id = uuid.uuid4()
    project = uuid.uuid4()
    callee = _callee(project)
    facade = AsyncMock()
    facade.get_run_project_id.return_value = project
    with patch("contexts.workflow.interfaces.facade.WorkflowFacade", return_value=facade):
        await svc._enforce_workflow_tenant(run_id, callee)  # no raise
