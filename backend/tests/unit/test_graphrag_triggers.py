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
    owner_kind: str = "agent_group",
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
        owner_kind=owner_kind,
    )


class _Repo:
    configs: ClassVar[list[GraphRagConfig]] = []
    seen_agent_ids: ClassVar[list[uuid.UUID]] = []
    seen_chatroom_id: ClassVar[uuid.UUID | None] = None

    def __init__(self, db) -> None:
        pass

    async def list_message_trigger_configs_for_room(self, *, chatroom_id, agent_ids):
        # F-3: the message-trigger path resolves room coverage, not the
        # agent-delete cascade. The fake records the scope it was called with so
        # the evaluator's threading of chatroom_id + agent_ids is asserted.
        self.__class__.seen_chatroom_id = chatroom_id
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
    chatroom_id = uuid.uuid4()
    cfg = _cfg(agent_id=agent_id, trigger_config={"every_n_messages": 2})
    _Repo.configs = [cfg]
    monkeypatch.setattr(trigger_mod, "GraphRagConfigRepository", _Repo)
    counter = _Counter([1, 2])

    first = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), chatroom_id=chatroom_id, agent_ids=[agent_id, agent_id], counter=counter
    )
    second = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), chatroom_id=chatroom_id, agent_ids=[agent_id], counter=counter
    )

    assert first == []
    assert second == [
        trigger_mod.GraphRagBuildTrigger(
            config_id=cfg.id,
            triggered_by="every_n_messages",
            job_id=f"graphrag:build:{cfg.id}:idle:0",
        )
    ]
    assert counter.seen == [cfg.id, cfg.id]
    assert _Repo.seen_agent_ids == [agent_id]
    # F-3: the evaluator resolves coverage by room, threading chatroom_id.
    assert _Repo.seen_chatroom_id == chatroom_id


@pytest.mark.asyncio
async def test_chatroom_and_workspace_owned_configs_fire(monkeypatch) -> None:
    # F-3 AC-2: message-count triggers fire for chatroom- and workspace-owned
    # maps the room selector returns — the deletion-cascade list_for_agents used
    # to omit both, leaving them inert. Here the room selector returns them, so
    # the evaluator increments and fires exactly like an agent_group map.
    agent_id = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    room_cfg = _cfg(trigger_config={"every_n_messages": 1}, owner_kind="chatroom")
    ws_cfg = _cfg(trigger_config={"every_n_messages": 1}, owner_kind="workspace")
    _Repo.configs = [room_cfg, ws_cfg]
    monkeypatch.setattr(trigger_mod, "GraphRagConfigRepository", _Repo)
    counter = _Counter([1, 1])

    fired = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), chatroom_id=chatroom_id, agent_ids=[agent_id], counter=counter
    )

    assert {t.config_id for t in fired} == {room_cfg.id, ws_cfg.id}
    assert counter.seen == [room_cfg.id, ws_cfg.id]


@pytest.mark.asyncio
async def test_manual_only_trigger_does_not_increment_counter(monkeypatch) -> None:
    cfg = _cfg(trigger_config={"manual": True})
    _Repo.configs = [cfg]
    monkeypatch.setattr(trigger_mod, "GraphRagConfigRepository", _Repo)
    counter = _Counter([1])

    fired = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), chatroom_id=uuid.uuid4(), agent_ids=[cfg.agent_id], counter=counter
    )

    assert fired == []
    assert counter.seen == []


# ---------------------------------------------------------------------------
# D5 (AC-8) — enqueue de-duplication job id
# ---------------------------------------------------------------------------


def test_build_job_id_stable_within_watermark_and_changes_after() -> None:
    from datetime import timedelta

    config_id = uuid.uuid4()
    t0 = datetime(2026, 7, 8, 9, 0, 0, tzinfo=UTC)

    id_a = trigger_mod.graphrag_build_job_id(config_id, last_build_state=BuildState.IDLE, last_build_at=t0)
    id_b = trigger_mod.graphrag_build_job_id(config_id, last_build_state=BuildState.IDLE, last_build_at=t0)
    # Two triggers within the same watermark collapse to one job id.
    assert id_a == id_b

    # After completion the started-at watermark advances → a fresh id, so the
    # rebuild is never suppressed by the prior job's retained result.
    id_next = trigger_mod.graphrag_build_job_id(
        config_id, last_build_state=BuildState.IDLE, last_build_at=t0 + timedelta(seconds=5)
    )
    assert id_next != id_a

    # A single failure flips the state, so the first retry gets a fresh id too.
    id_failed = trigger_mod.graphrag_build_job_id(
        config_id, last_build_state=BuildState.FAILED, last_build_at=t0
    )
    assert id_failed != id_a

    # A never-built config has a stable id (epoch 0).
    first = trigger_mod.graphrag_build_job_id(config_id, last_build_state=BuildState.IDLE, last_build_at=None)
    assert first == f"graphrag:build:{config_id}:idle:0"


@pytest.mark.asyncio
async def test_non_buildable_state_does_not_increment_counter(monkeypatch) -> None:
    cfg = _cfg(trigger_config={"every_n_messages": 1}, state=BuildState.RUNNING)
    _Repo.configs = [cfg]
    monkeypatch.setattr(trigger_mod, "GraphRagConfigRepository", _Repo)
    counter = _Counter([1])

    fired = await trigger_mod.evaluate_graphrag_message_triggers(
        object(), chatroom_id=uuid.uuid4(), agent_ids=[cfg.agent_id], counter=counter
    )

    assert fired == []
    assert counter.seen == []
