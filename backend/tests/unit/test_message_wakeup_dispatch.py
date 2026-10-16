"""Dedup of the room-binding fetch shared by the message send endpoint's two
wake-up evaluations (every_n_messages + @mention).

A message that carries @mentions used to query the room binding twice — once in
``evaluate_message_wakeups`` and once in ``filter_mentioned_bound_agents``.
The send path now fetches once via ``list_bound_agents`` (rows with roles,
O-2/R28.04) and passes rows / ids into the evaluators.
"""

from __future__ import annotations

import logging
import uuid

import pytest

import app.api.v1.messages as messages_mod
import contexts.conversation.application.triggers as triggers
import contexts.orchestration.interfaces.facade as facade_mod


class _BoomRepo:
    """Repository that explodes if constructed — proves no query was issued."""

    def __init__(self, db) -> None:
        raise AssertionError("must not re-query when the binding is supplied")


@pytest.mark.asyncio
async def test_filter_mentioned_bound_agents_narrows_with_provided_binding(monkeypatch) -> None:
    """The caller-supplied binding still narrows the candidate set, but the
    role-aware fetch is unconditional (R28.04) — the fake repo below IS
    queried; only ids present in both survive."""
    from types import SimpleNamespace

    from contexts.conversation.domain.models import ChatroomAgentRole

    a1, a2 = uuid.uuid4(), uuid.uuid4()

    class _RoleRepo:
        def __init__(self, db) -> None:
            pass

        async def list(self, chatroom_id):
            return [SimpleNamespace(agent_id=a, role=ChatroomAgentRole.NORMAL) for a in (a1, a2)]

    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _RoleRepo)

    out = await triggers.filter_mentioned_bound_agents(
        object(),
        chatroom_id=uuid.uuid4(),
        mention_agent_ids=[a1, a2],
        bound_agent_ids=[a1],  # shared fetch — a2 is not in the narrowed set
    )
    assert out == [a1]


@pytest.mark.asyncio
async def test_evaluate_message_wakeups_uses_provided_binding(monkeypatch) -> None:
    from contexts.conversation.domain.models import BindingWakeupState, ChatroomAgent, ChatroomAgentRole

    a1, room = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _BoomRepo)
    layers = BindingWakeupState(
        chatroom_id=room,
        agent_id=a1,
        binding_id=uuid.uuid4(),
        role=ChatroomAgentRole.NORMAL,
        version=1,
        room_overrides={},
        runtime_overrides={},
        runtime_started_at=None,
    )
    passed: list = []

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def on_message_created(
            self,
            *,
            room_id,
            sender_is_user,
            sender_agent_id=None,
            agent_ids,
            observer_agent_ids=frozenset(),
            states=None,
        ):
            passed.append(states)
            return list(agent_ids)

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)
    woken = await triggers.evaluate_message_wakeups(
        object(),
        chatroom_id=room,
        sender_is_user=True,
        bound_agents=[ChatroomAgent(chatroom_id=room, agent_id=a1, wakeup=layers)],
    )
    assert woken == [a1]
    # Code review #9: the rows' own layers go through, so nothing reads them again.
    assert passed == [[layers]]


@pytest.mark.asyncio
async def test_dispatch_graphrag_builds_enqueues_fired_configs(monkeypatch) -> None:
    chatroom_id = uuid.uuid4()
    config_id = uuid.uuid4()
    enqueued: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Trigger:
        def __init__(self) -> None:
            self.config_id = config_id
            self.triggered_by = "every_n_messages"
            self.job_id = f"graphrag:build:{config_id}:idle:0"

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def evaluate_graphrag_message_triggers(self, *, chatroom_id):
            # F-3: the dispatcher threads the sending room through so coverage is
            # resolved by room, not the agent-delete cascade.
            assert chatroom_id == expected_room
            return [_Trigger()]

    async def _enqueue(*args, **kwargs) -> None:
        enqueued.append((args, kwargs))

    monkeypatch.setattr(messages_mod, "KnowledgeFacade", _Facade)
    monkeypatch.setattr(messages_mod, "enqueue", _enqueue)

    expected_room = chatroom_id
    await messages_mod._dispatch_graphrag_builds(object(), chatroom_id)

    # D5: the enqueue carries the dedup job id so concurrent triggers for the
    # same config+watermark collapse to a single queued build.
    assert enqueued == [
        (
            ("graphrag_build",),
            {
                "config_id": str(config_id),
                "triggered_by": "every_n_messages",
                "_job_id": f"graphrag:build:{config_id}:idle:0",
            },
        )
    ]


@pytest.mark.asyncio
async def test_dispatch_graphrag_builds_evaluates_agentless_room(monkeypatch) -> None:
    # R11.02/R11.08: a committed message in a room with no bound Agent still
    # reaches room-scoped trigger evaluation. Coverage is resolved from the room,
    # so an empty binding set is not a reason to skip the facade.
    chatroom_id = uuid.uuid4()
    config_id = uuid.uuid4()
    enqueued: list[tuple[tuple[object, ...], dict[str, object]]] = []
    calls: list[uuid.UUID] = []

    class _Trigger:
        def __init__(self) -> None:
            self.config_id = config_id
            self.triggered_by = "every_n_messages"
            self.job_id = f"graphrag:build:{config_id}:idle:0"

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def evaluate_graphrag_message_triggers(self, *, chatroom_id):
            calls.append(chatroom_id)
            return [_Trigger()]

    async def _enqueue(*args, **kwargs) -> None:
        enqueued.append((args, kwargs))

    monkeypatch.setattr(messages_mod, "KnowledgeFacade", _Facade)
    monkeypatch.setattr(messages_mod, "enqueue", _enqueue)

    await messages_mod._dispatch_graphrag_builds(object(), chatroom_id)

    assert calls == [chatroom_id]
    assert enqueued == [
        (
            ("graphrag_build",),
            {
                "config_id": str(config_id),
                "triggered_by": "every_n_messages",
                "_job_id": f"graphrag:build:{config_id}:idle:0",
            },
        )
    ]


@pytest.mark.asyncio
async def test_dispatch_message_wakeups_enqueues_with_trigger_message_id(monkeypatch) -> None:
    from contexts.orchestration.interfaces.facade import WakeTarget

    agent_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    room_id = uuid.uuid4()
    trigger_message_id = uuid.uuid4()
    enqueued: list[tuple[object, ...]] = []

    async def _evaluate_message_wakeups(*_a, **_k):
        return [WakeTarget(agent_id, binding_id)]

    async def _enqueue(*args, **_kwargs) -> None:
        enqueued.append(args)

    monkeypatch.setattr(messages_mod, "evaluate_message_wakeups", _evaluate_message_wakeups)
    monkeypatch.setattr(messages_mod, "enqueue", _enqueue)

    woken = await messages_mod._dispatch_message_wakeups(
        object(), room_id, [agent_id], trigger_message_id=trigger_message_id
    )

    assert woken == {agent_id}
    # The binding that fired rides along, so a rebind in between drops the job ([R15.09]).
    assert enqueued == [
        (
            "wakeup_agent",
            str(agent_id),
            str(room_id),
            "every_n_messages",
            str(trigger_message_id),
            str(binding_id),
        )
    ]


@pytest.mark.asyncio
async def test_dispatch_mention_wakeups_enqueues_with_trigger_message_id(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    room_id = uuid.uuid4()
    trigger_message_id = uuid.uuid4()
    enqueued: list[tuple[object, ...]] = []

    async def _filter_mentioned_bound_agents(*_a, **_k):
        return [agent_id]

    async def _enqueue(*args, **_kwargs) -> None:
        enqueued.append(args)

    monkeypatch.setattr(messages_mod, "filter_mentioned_bound_agents", _filter_mentioned_bound_agents)
    monkeypatch.setattr(messages_mod, "enqueue", _enqueue)

    await messages_mod._dispatch_mention_wakeups(
        object(),
        room_id,
        [agent_id],
        already_woken=set(),
        trigger_message_id=trigger_message_id,
    )

    assert enqueued == [("wakeup_agent", str(agent_id), str(room_id), "mention", str(trigger_message_id))]


# --------------------------------------------------------------------------- #
# AC-1 (docs/tasks/2026-07-27-wakeup-config-type-validation): one agent's
# wake-up evaluation failing must not empty the room's wake list for every
# other bound agent. Exercised via a facade read failure rather than a
# wrong-typed `wakeup_config` value: AC-2 (test_wakeup_service.py) already
# makes `WakeupConfig.from_dict` total, so a parse-time `TypeError` is no
# longer reachable through the config shape. Q-1's own rationale is that the
# loop must be guarded regardless — it can still raise from Redis or a facade
# read — so this simulates exactly that aggravating case.
# --------------------------------------------------------------------------- #


async def test_one_unparseable_config_does_not_stop_the_room(monkeypatch, caplog) -> None:
    from types import SimpleNamespace

    from contexts.orchestration.application.wakeup_service import WakeupService

    def _async_return(value):
        async def _f(*_a, **_k):
            return value

        return _f

    def _agent() -> SimpleNamespace:
        wakeup_config = {
            "triggers": {"every_n_messages": {"enabled": True, "n": 1}},
            "allow_self_open": False,
        }
        return SimpleNamespace(id=uuid.uuid4(), deleted_at=None, wakeup_config=wakeup_config)

    a, b, c = _agent(), _agent(), _agent()
    rolled_back: list[bool] = []

    async def _rollback() -> None:
        rolled_back.append(True)

    async def _get_agent(agent_id: uuid.UUID) -> SimpleNamespace:
        if agent_id == b.id:
            raise RuntimeError("facade read failed")
        return {a.id: a, c.id: c}[agent_id]

    from contexts.conversation.domain.models import BindingWakeupState, ChatroomAgentRole
    from contexts.orchestration.application.room_wakeup_service import RoomWakeupService

    def _state(room_id, agent_id):
        return BindingWakeupState(
            chatroom_id=room_id,
            agent_id=agent_id,
            binding_id=agent_id,
            role=ChatroomAgentRole.NORMAL,
            version=1,
            room_overrides={},
            runtime_overrides={},
            runtime_started_at=None,
        )

    async def _states(room_id):
        return [_state(room_id, x.id) for x in (a, b, c)]

    svc = WakeupService.__new__(WakeupService)
    svc._db = SimpleNamespace(rollback=_rollback)
    room_wakeup = RoomWakeupService.__new__(RoomWakeupService)
    room_wakeup._agents = SimpleNamespace(get_agent=_get_agent)
    svc._room_wakeup = room_wakeup
    svc._conversation = SimpleNamespace(room_binding_wakeup_states=_states)
    svc._presence = SimpleNamespace(list_room=_async_return([uuid.uuid4()]))

    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.touch_silence_timestamp",
        _async_return(None),
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.reset_autostop",
        _async_return(None),
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.increment_message_count",
        _async_return(1),
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.pop_fire_due",
        _async_return(False),
    )

    with caplog.at_level(logging.WARNING):
        woken = await svc.on_message_created(
            room_id=uuid.uuid4(),
            sender_is_user=True,
            agent_ids=[a.id, b.id, c.id],
        )

    assert [t.agent_id for t in woken] == [a.id, c.id]
    assert rolled_back == [True]
    assert any(str(b.id) in record.getMessage() for record in caplog.records)
