"""F-18 (R11.14) — soft-deleting a RAG / Knowledge Map config unbinds its agents.

The DB ``ON DELETE SET NULL`` FK (migrations 0012 / 0048) unbinds attached agents
only on a physical row DELETE; a config *soft*-delete is an ``UPDATE deleted_at``,
so the FK never fires and every attached agent is stranded with a dangling
``rag_config_id`` / ``knowmap_config_id``. The delete services must null the
binding explicitly, through the agents facade, in the same transaction — and the
RAG path must also disable the dependent File Search tool.

Three layers are pinned here, matching the repo's conventions:

* statement level (like ``test_graphrag_soft_delete_owner_clear``) — the repo
  UPDATEs null the right column, scoped by project + config, and the tool UPDATE
  disables only ``hosted_file_search``;
* application orchestration — ``AgentService.clear_config_bindings`` reconciles
  the File Search tool for RAG and not for Knowledge Map;
* delete-service wiring (like ``test_knowmap_config_service``) — ``soft_delete``
  calls the facade with the config's own project scope and records the unbound
  ids in the delete audit metadata.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from contexts.agents.application.agent_service import AgentService
from contexts.agents.infrastructure.repositories import AgentRepository, AgentToolRepository

# --------------------------------------------------------------------------
# Statement-level: the repo UPDATEs (mocked session, compiled SQL assertions)
# --------------------------------------------------------------------------


class _Result:
    def all(self) -> list[Any]:
        return []


class _CapturingDb:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> _Result:
        self.statements.append(stmt)
        return _Result()


def _sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(" ", "")


@pytest.mark.asyncio
async def test_clear_rag_config_nulls_column_scoped_by_project() -> None:
    db = _CapturingDb()
    repo = AgentRepository(db)  # type: ignore[arg-type]
    project_id = uuid.uuid4()
    config_id = uuid.uuid4()

    await repo.clear_rag_config(project_id=project_id, rag_config_id=config_id)

    assert len(db.statements) == 1
    sql = _sql(db.statements[0])
    assert "rag_config_id=NULL" in sql, "the UPDATE must null rag_config_id"
    # Scoped by project + config + active — never a cross-tenant unbind (AC-5).
    # Compiled UUID literals render as dashless hex.
    assert project_id.hex in sql
    assert config_id.hex in sql
    assert "deleted_atISNULL" in sql


@pytest.mark.asyncio
async def test_clear_knowmap_config_nulls_column_scoped_by_project() -> None:
    db = _CapturingDb()
    repo = AgentRepository(db)  # type: ignore[arg-type]
    project_id = uuid.uuid4()
    config_id = uuid.uuid4()

    await repo.clear_knowmap_config(project_id=project_id, knowmap_config_id=config_id)

    assert len(db.statements) == 1
    sql = _sql(db.statements[0])
    assert "knowmap_config_id=NULL" in sql
    assert project_id.hex in sql
    assert config_id.hex in sql
    assert "deleted_atISNULL" in sql


@pytest.mark.asyncio
async def test_disable_file_search_targets_only_that_singleton() -> None:
    db = _CapturingDb()
    repo = AgentToolRepository(db)  # type: ignore[arg-type]
    agent_ids = [uuid.uuid4(), uuid.uuid4()]

    await repo.disable_file_search_for_agents(agent_ids)

    assert len(db.statements) == 1
    sql = _sql(db.statements[0]).lower()
    assert "enabled=false" in sql
    assert "hosted_file_search" in sql
    for aid in agent_ids:
        assert aid.hex in sql


@pytest.mark.asyncio
async def test_disable_file_search_noop_on_empty() -> None:
    db = _CapturingDb()
    repo = AgentToolRepository(db)  # type: ignore[arg-type]

    await repo.disable_file_search_for_agents([])

    assert db.statements == []  # no UPDATE emitted for an empty id list


# --------------------------------------------------------------------------
# Application orchestration: clear_config_bindings tool reconciliation
# --------------------------------------------------------------------------


def _service_with_mocked_repos() -> AgentService:
    svc = AgentService(AsyncMock())
    svc._agents = AsyncMock()
    svc._tools = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_rag_unbind_disables_file_search_and_returns_ids() -> None:
    svc = _service_with_mocked_repos()
    project_id = uuid.uuid4()
    config_id = uuid.uuid4()
    unbound = [uuid.uuid4()]
    svc._agents.clear_rag_config.return_value = unbound

    result = await svc.clear_config_bindings(project_id=project_id, rag_config_id=config_id)

    assert result == unbound
    svc._agents.clear_rag_config.assert_awaited_once_with(project_id=project_id, rag_config_id=config_id)
    # AC-6: the dependent File Search tool is disabled for every unbound agent.
    svc._tools.disable_file_search_for_agents.assert_awaited_once_with(unbound)
    svc._agents.clear_knowmap_config.assert_not_called()


@pytest.mark.asyncio
async def test_rag_unbind_with_no_agents_skips_tool_reconcile() -> None:
    svc = _service_with_mocked_repos()
    svc._agents.clear_rag_config.return_value = []

    result = await svc.clear_config_bindings(project_id=uuid.uuid4(), rag_config_id=uuid.uuid4())

    assert result == []
    svc._tools.disable_file_search_for_agents.assert_not_called()


@pytest.mark.asyncio
async def test_knowmap_unbind_performs_no_tool_change() -> None:
    svc = _service_with_mocked_repos()
    project_id = uuid.uuid4()
    config_id = uuid.uuid4()
    unbound = [uuid.uuid4(), uuid.uuid4()]
    svc._agents.clear_knowmap_config.return_value = unbound

    result = await svc.clear_config_bindings(project_id=project_id, knowmap_config_id=config_id)

    assert result == unbound
    svc._agents.clear_knowmap_config.assert_awaited_once_with(
        project_id=project_id, knowmap_config_id=config_id
    )
    # AC-6: knowmap has no dependent tool — no File Search reconciliation.
    svc._tools.disable_file_search_for_agents.assert_not_called()
    svc._agents.clear_rag_config.assert_not_called()


# --------------------------------------------------------------------------
# Delete-service wiring: soft_delete unbinds via the facade + audits the ids
# --------------------------------------------------------------------------


class _FakeAgentsFacade:
    """Captures the clear_config_bindings call and returns a preset id list."""

    calls: ClassVar[list[dict[str, Any]]] = []
    returns: ClassVar[list[uuid.UUID]] = []

    def __init__(self, _db: Any) -> None:
        pass

    async def clear_config_bindings(self, **kw: Any) -> list[uuid.UUID]:
        _FakeAgentsFacade.calls.append(kw)
        return list(_FakeAgentsFacade.returns)


def _patch_facade(returns: list[uuid.UUID]) -> Any:
    _FakeAgentsFacade.calls = []
    _FakeAgentsFacade.returns = returns
    return patch("contexts.agents.interfaces.facade.AgentsFacade", _FakeAgentsFacade)


class _NoDocs:
    """Document repo stub: the config has no child documents to cascade."""

    def __init__(self, _db: Any) -> None:
        pass

    async def list_for_config(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    async def delete(self, *_a: Any, **_k: Any) -> None:  # pragma: no cover - never reached
        raise AssertionError("no documents to delete")


@pytest.mark.asyncio
async def test_rag_soft_delete_unbinds_agents_and_audits_ids() -> None:
    from contexts.knowledge.application.config_service import RagConfigService

    project_id = uuid.uuid4()
    cfg = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    unbound = uuid.uuid4()

    svc = RagConfigService(AsyncMock())
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    # soft_delete cascades via the service's own document repo (self._documents);
    # this config has no child documents to drain.
    svc._documents = _NoDocs(None)  # type: ignore[assignment]
    emit = AsyncMock()
    with (
        _patch_facade([unbound]),
        patch("contexts.knowledge.application.config_service.audit.emit", new=emit),
    ):
        await svc.soft_delete(config_id=cfg.id, actor_user_id=uuid.uuid4(), actor_ip=None)

    # AC-3/AC-5: unbind runs against the config's OWN project scope.
    call = _FakeAgentsFacade.calls[0]
    assert call == {"project_id": project_id, "rag_config_id": cfg.id}
    # AC-4: the unbound id is recorded in the delete audit metadata.
    meta = emit.await_args.args[1].metadata
    assert meta["unbound_agents"] == [str(unbound)]
    # The soft-delete write still happens, in the same call path.
    svc._configs.soft_delete.assert_awaited_once_with(cfg.id)


@pytest.mark.asyncio
async def test_knowmap_soft_delete_unbinds_agents_and_audits_ids() -> None:
    from contexts.knowledge.application.knowmap_config_service import KnowmapConfigService

    project_id = uuid.uuid4()
    cfg = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    unbound = uuid.uuid4()

    svc = KnowmapConfigService(AsyncMock())
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    emit = AsyncMock()
    with (
        _patch_facade([unbound]),
        patch("contexts.knowledge.application.knowmap_config_service.KnowmapDocumentRepository", _NoDocs),
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=emit),
    ):
        await svc.soft_delete(config_id=cfg.id, actor_user_id=uuid.uuid4(), actor_ip=None)

    call = _FakeAgentsFacade.calls[0]
    assert call == {"project_id": project_id, "knowmap_config_id": cfg.id}
    meta = emit.await_args.args[1].metadata
    assert meta["unbound_agents"] == [str(unbound)]
    svc._configs.soft_delete.assert_awaited_once_with(cfg.id)
