"""Dedup of the room-binding fetch shared by the message send endpoint's two
wake-up evaluations (every_n_messages + @mention).

A message that carries @mentions used to query the room binding twice — once in
``evaluate_message_wakeups`` and once in ``filter_mentioned_bound_agents``. Both
now accept a pre-fetched ``bound_agent_ids`` so the send path fetches once.
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
async def test_filter_mentioned_bound_agents_uses_provided_binding(monkeypatch) -> None:
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _BoomRepo)

    out = await triggers.filter_mentioned_bound_agents(
        object(),
        chatroom_id=uuid.uuid4(),
        mention_agent_ids=[a1, a2],
        bound_agent_ids=[a1],  # shared fetch — a2 is not bound
    )
    assert out == [a1]


@pytest.mark.asyncio
async def test_evaluate_message_wakeups_uses_provided_binding(monkeypatch) -> None:
    a1 = uuid.uuid4()
    monkeypatch.setattr(triggers, "ChatroomAgentRepository", _BoomRepo)

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def on_message_created(self, *, room_id, sender_is_user, agent_ids):
            return list(agent_ids)

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)
    woken = await triggers.evaluate_message_wakeups(
        object(), chatroom_id=uuid.uuid4(), sender_is_user=True, bound_agent_ids=[a1]
    )
    assert woken == [a1]


@pytest.mark.asyncio
async def test_dispatch_graphrag_builds_enqueues_fired_configs(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    config_id = uuid.uuid4()
    enqueued: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Trigger:
        def __init__(self) -> None:
            self.config_id = config_id
            self.triggered_by = "every_n_messages"

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def evaluate_graphrag_message_triggers(self, *, agent_ids):
            assert agent_ids == [agent_id]
            return [_Trigger()]

    async def _enqueue(*args, **kwargs) -> None:
        enqueued.append((args, kwargs))

    monkeypatch.setattr(messages_mod, "KnowledgeFacade", _Facade)
    monkeypatch.setattr(messages_mod, "enqueue", _enqueue)

    await messages_mod._dispatch_graphrag_builds(object(), uuid.uuid4(), [agent_id])

    assert enqueued == [
        (
            ("graphrag_build",),
            {"config_id": str(config_id), "triggered_by": "every_n_messages"},
        )
    ]
