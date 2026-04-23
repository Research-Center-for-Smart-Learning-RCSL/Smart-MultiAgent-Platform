"""Unit tests for :class:`McpBindingService` — E.9."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.agents.application.mcp_service import McpBindingService
from contexts.agents.domain.errors import (
    McpBindingNotFound,
    McpEgressDenied,
    McpTestFailed,
    McpTimeout,
)
from contexts.agents.domain.mcp import McpTestResult
from contexts.agents.domain.models import McpBinding, McpSource


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)

        class _R:
            def first(self_inner: Any) -> None:
                return None

            def all(self_inner: Any) -> list[Any]:
                return []

            def one(self_inner: Any) -> None:
                return None

        return _R()


class _FakeBindingRepo:
    def __init__(self, bindings: list[McpBinding]) -> None:
        self._bindings = list(bindings)
        self.removed: list[uuid.UUID] = []

    async def list(self, agent_id: uuid.UUID):
        return [b for b in self._bindings if b.agent_id == agent_id]

    async def add(
        self,
        *,
        agent_id: uuid.UUID,
        source: McpSource,
        reference: str,
        allowed_tools,
        config: dict[str, Any],
    ) -> McpBinding:
        b = McpBinding(
            id=uuid.uuid4(),
            agent_id=agent_id,
            source=source,
            reference=reference,
            allowed_tools=tuple(allowed_tools),
            config=config,
            created_at=datetime.now(tz=UTC),
        )
        self._bindings.append(b)
        return b

    async def remove(self, *, agent_id: uuid.UUID, binding_id: uuid.UUID) -> None:
        for b in self._bindings:
            if b.id == binding_id and b.agent_id == agent_id:
                self._bindings.remove(b)
                self.removed.append(binding_id)
                return
        raise McpBindingNotFound(str(binding_id))


class _FakeRunner:
    def __init__(self, *, result: McpTestResult | None = None,
                 raises: BaseException | None = None) -> None:
        self.result = result
        self.raises = raises
        self.probe_calls: list[dict[str, Any]] = []

    async def probe(self, **kwargs: Any) -> McpTestResult:
        self.probe_calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        assert self.result is not None
        return self.result

    async def invoke_mcp_tool(self, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError

    async def run_file_op(self, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError

    async def run_code_exec(self, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError


def _binding(agent_id: uuid.UUID, *, config: dict[str, Any] | None = None) -> McpBinding:
    return McpBinding(
        id=uuid.uuid4(),
        agent_id=agent_id,
        source=McpSource.URL,
        reference="https://example.com/mcp",
        allowed_tools=("list", "fetch"),
        config=config or {},
        created_at=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_list_delegates_to_repo() -> None:
    agent_id = uuid.uuid4()
    b = _binding(agent_id)
    svc = McpBindingService(_FakeSession())  # type: ignore[arg-type]
    svc._repo = _FakeBindingRepo([b])  # type: ignore[assignment]
    rows = await svc.list(agent_id)
    assert [x.id for x in rows] == [b.id]


@pytest.mark.asyncio
async def test_remove_deletes_and_audits() -> None:
    agent_id = uuid.uuid4()
    b = _binding(agent_id)
    session = _FakeSession()
    svc = McpBindingService(session)  # type: ignore[arg-type]
    repo = _FakeBindingRepo([b])
    svc._repo = repo  # type: ignore[assignment]
    await svc.remove(
        agent_id=agent_id, binding_id=b.id,
        actor_user_id=uuid.uuid4(), actor_ip="127.0.0.1",
    )
    assert repo.removed == [b.id]
    # One audit insert issued.
    assert session.executed, "expected audit row to be inserted"


@pytest.mark.asyncio
async def test_test_endpoint_calls_runner_probe_ok() -> None:
    agent_id = uuid.uuid4()
    project_id = uuid.uuid4()
    b = _binding(agent_id)
    runner = _FakeRunner(
        result=McpTestResult(ok=True, tool_names=("list", "fetch"), duration_ms=42),
    )
    svc = McpBindingService(_FakeSession(), runner=runner)  # type: ignore[arg-type]
    svc._repo = _FakeBindingRepo([b])  # type: ignore[assignment]

    result = await svc.test(
        agent_id=agent_id, binding_id=b.id, project_id=project_id,
        actor_user_id=uuid.uuid4(), actor_ip=None,
    )
    assert result.ok
    assert result.tool_names == ("list", "fetch")
    assert runner.probe_calls and runner.probe_calls[0]["reference"] == b.reference


@pytest.mark.asyncio
async def test_test_endpoint_unknown_binding() -> None:
    agent_id = uuid.uuid4()
    svc = McpBindingService(_FakeSession(), runner=_FakeRunner())  # type: ignore[arg-type]
    svc._repo = _FakeBindingRepo([])  # type: ignore[assignment]
    with pytest.raises(McpBindingNotFound):
        await svc.test(
            agent_id=agent_id, binding_id=uuid.uuid4(), project_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(), actor_ip=None,
        )


@pytest.mark.asyncio
async def test_test_endpoint_surfaces_egress_denial() -> None:
    agent_id = uuid.uuid4()
    b = _binding(agent_id)
    runner = _FakeRunner(raises=McpEgressDenied("blocked"))
    svc = McpBindingService(_FakeSession(), runner=runner)  # type: ignore[arg-type]
    svc._repo = _FakeBindingRepo([b])  # type: ignore[assignment]
    with pytest.raises(McpEgressDenied):
        await svc.test(
            agent_id=agent_id, binding_id=b.id, project_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(), actor_ip=None,
        )


@pytest.mark.asyncio
async def test_test_endpoint_surfaces_timeout() -> None:
    agent_id = uuid.uuid4()
    b = _binding(agent_id)
    runner = _FakeRunner(raises=McpTimeout("slow"))
    svc = McpBindingService(_FakeSession(), runner=runner)  # type: ignore[arg-type]
    svc._repo = _FakeBindingRepo([b])  # type: ignore[assignment]
    with pytest.raises(McpTimeout):
        await svc.test(
            agent_id=agent_id, binding_id=b.id, project_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(), actor_ip=None,
        )


@pytest.mark.asyncio
async def test_test_endpoint_wraps_unexpected_as_failed() -> None:
    agent_id = uuid.uuid4()
    b = _binding(agent_id)
    runner = _FakeRunner(raises=RuntimeError("bad wire proto"))
    svc = McpBindingService(_FakeSession(), runner=runner)  # type: ignore[arg-type]
    svc._repo = _FakeBindingRepo([b])  # type: ignore[assignment]
    with pytest.raises(McpTestFailed):
        await svc.test(
            agent_id=agent_id, binding_id=b.id, project_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(), actor_ip=None,
        )
