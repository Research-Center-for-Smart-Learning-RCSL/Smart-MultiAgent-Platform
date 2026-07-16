"""Agent-reply Concept Map dispatch — room-scoped, never binding-gated.

R11.02/R11.08: an agent reply is activity in the room, so it must reach
room-level Concept Map trigger evaluation even when the room's binding set comes
back empty — the agentless-room case, and the race where the last member unbinds
between the reply's commit and this post-commit dispatch.
"""

from __future__ import annotations

import sys
import uuid
from types import SimpleNamespace

import pytest

from contexts.agents.application.runtime.turn_engine import TurnEngine


def _engine() -> TurnEngine:
    # The dispatch under test touches only ``self._db``, which it hands to the
    # (monkeypatched) collaborators; a full TurnEngine build would drag in the
    # provider/registry graph for no added coverage.
    engine = object.__new__(TurnEngine)
    engine._db = object()
    return engine


@pytest.mark.asyncio
async def test_agent_reply_evaluates_concept_maps_with_no_bound_agents(monkeypatch) -> None:
    chatroom_id = uuid.uuid4()
    config_id = uuid.uuid4()
    enqueued: list[tuple[tuple[object, ...], dict[str, object]]] = []
    evaluated: list[uuid.UUID] = []

    async def _list_bound_agents(db, room_id):
        return []

    async def _evaluate_message_wakeups(db, **kwargs):
        return []

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def evaluate_graphrag_message_triggers(self, *, chatroom_id):
            evaluated.append(chatroom_id)
            return [
                SimpleNamespace(
                    config_id=config_id,
                    triggered_by="every_n_messages",
                    job_id=f"graphrag:build:{config_id}:idle:0",
                )
            ]

    async def _enqueue(*args, **kwargs) -> None:
        enqueued.append((args, kwargs))

    triggers_mod = sys.modules["contexts.conversation.application.triggers"]
    facade_mod = sys.modules["contexts.knowledge.interfaces.facade"]
    queue_mod = sys.modules["shared_kernel.queue"]
    monkeypatch.setattr(triggers_mod, "list_bound_agents", _list_bound_agents)
    monkeypatch.setattr(triggers_mod, "evaluate_message_wakeups", _evaluate_message_wakeups)
    monkeypatch.setattr(facade_mod, "KnowledgeFacade", _Facade)
    monkeypatch.setattr(queue_mod, "enqueue", _enqueue)

    await _engine()._dispatch_agent_reply_wakeups(SimpleNamespace(id=uuid.uuid4()), chatroom_id, uuid.uuid4())

    # An empty binding set is not a reason to skip room-level evaluation.
    assert evaluated == [chatroom_id]
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
