from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

import pytest

import contexts.knowledge.application.graphrag_triggers as trigger_mod
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig


def _cfg(
    *,
    agent_id: uuid.UUID | None = None,
    trigger_config: dict[str, object] | None = None,
    state: BuildState = BuildState.IDLE,
) -> GraphRagConfig:
    return GraphRagConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=agent_id or uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        trigger_config=trigger_config or {},
        last_build_at=None,
        last_build_state=state,
        last_build_error=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


class _Repo:
    configs: ClassVar[list[GraphRagConfig]] = []
    seen_agent_ids: ClassVar[list[uuid.UUID]] = []

    def __init__(self, db) -> None:
        pass

    async def list_for_agents(self, agent_ids):
        self.__class__.seen_agent_ids = list(agent_ids)
        return list(self.__class__.configs)


class _Counter:
    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self.seen: list[uuid.UUID] = []

    async def increment(self, config_id: uuid.UUID) -> int:
        self.seen.append(config_id)
        return self._counts.pop(0)


@pytest.mark.asyncio
async def test_every_n_messages_fires_on_counter_boundary(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    cfg = _cfg(agent_id=agent_id, trigger_config={"every_n_messages": 2})
    _Repo.configs = [cfg]
    monkeypatch.setattr(trigger_mod, "GraphRagConfigRepository", _Repo)
    counter = _Counter([1, 2])

    first = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), agent_ids=[agent_id, agent_id], counter=counter
    )
    second = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), agent_ids=[agent_id], counter=counter
    )

    assert first == []
    assert second == [
        trigger_mod.GraphRagBuildTrigger(
            config_id=cfg.id,
            triggered_by="every_n_messages",
        )
    ]
    assert counter.seen == [cfg.id, cfg.id]
    assert _Repo.seen_agent_ids == [agent_id]


@pytest.mark.asyncio
async def test_manual_only_trigger_does_not_increment_counter(monkeypatch) -> None:
    cfg = _cfg(trigger_config={"manual": True})
    _Repo.configs = [cfg]
    monkeypatch.setattr(trigger_mod, "GraphRagConfigRepository", _Repo)
    counter = _Counter([1])

    fired = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), agent_ids=[cfg.agent_id], counter=counter
    )

    assert fired == []
    assert counter.seen == []


@pytest.mark.asyncio
async def test_non_buildable_state_does_not_increment_counter(monkeypatch) -> None:
    cfg = _cfg(trigger_config={"every_n_messages": 1}, state=BuildState.RUNNING)
    _Repo.configs = [cfg]
    monkeypatch.setattr(trigger_mod, "GraphRagConfigRepository", _Repo)
    counter = _Counter([1])

    fired = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), agent_ids=[cfg.agent_id], counter=counter
    )

    assert fired == []
    assert counter.seen == []
