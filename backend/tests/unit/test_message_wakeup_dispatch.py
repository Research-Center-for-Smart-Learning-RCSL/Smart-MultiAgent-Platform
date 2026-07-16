"""Dedup of the room-binding fetch shared by the message send endpoint's two
wake-up evaluations (every_n_messages + @mention).

A message that carries @mentions used to query the room binding twice — once in
``evaluate_message_wakeups`` and once in ``filter_mentioned_bound_agents``.
The send path now fetches once via ``list_bound_agents`` (rows with roles,
O-2/R28.04) and passes rows / ids into the evaluators.
"""

from __future__ import annotations

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
    from types import SimpleNamespace

    from contexts.conversation.domain.models import ChatroomAgentRole

    a1 = uuid.uuid4()
    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _BoomRepo)

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def on_message_created(
            self, *, room_id, sender_is_user, sender_agent_id=None, agent_ids, observer_agent_ids=frozenset()
        ):
            return list(agent_ids)

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)
    woken = await triggers.evaluate_message_wakeups(
        object(),
        chatroom_id=uuid.uuid4(),
        sender_is_user=True,
        bound_agents=[SimpleNamespace(agent_id=a1, role=ChatroomAgentRole.NORMAL)],
    )
    assert woken == [a1]


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
    agent_id = uuid.uuid4()
    room_id = uuid.uuid4()
    trigger_message_id = uuid.uuid4()
    enqueued: list[tuple[object, ...]] = []

    async def _evaluate_message_wakeups(*_a, **_k):
        return [agent_id]

    async def _enqueue(*args, **_kwargs) -> None:
        enqueued.append(args)

    monkeypatch.setattr(messages_mod, "evaluate_message_wakeups", _evaluate_message_wakeups)
    monkeypatch.setattr(messages_mod, "enqueue", _enqueue)

    woken = await messages_mod._dispatch_message_wakeups(
        object(), room_id, [agent_id], trigger_message_id=trigger_message_id
    )

    assert woken == {agent_id}
    assert enqueued == [
        ("wakeup_agent", str(agent_id), str(room_id), "every_n_messages", str(trigger_message_id))
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
