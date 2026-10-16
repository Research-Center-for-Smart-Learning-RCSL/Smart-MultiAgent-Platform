"""A turn's `update_wakeup` timers wait for the turn's commit (phase 2 D-14, FU-9).

The tool writes the binding row on the turn's own session and queues the timer
effects in ``WakeupBinding.effect_sink``. These drive the real ``_run_locked``
and assert the effects reach Redis only after the turn's last commit, on each
branch that commits, and never on the failure path, which rolls the write back.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import contexts.agents.application.runtime.turn_engine as te
import contexts.orchestration.interfaces.facade as orch_facade
from contexts.agents.application.runtime.tool_registry import WakeupBinding
from contexts.agents.application.runtime.turn_engine import ToolLoopOutcome
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.orchestration.interfaces.facade import TimerEffects
from tests.unit.turn_engine_fakes import NOW, make_agent, run_locked, wire_engine

_ROOM = uuid.uuid4()


def _wire(monkeypatch: pytest.MonkeyPatch, *, text: str, role: ChatroomAgentRole = ChatroomAgentRole.NORMAL):
    agent = make_agent()
    engine, _trace = wire_engine(monkeypatch, agent, note={}, role=role)
    effect = TimerEffects(agent_id=agent.id, room_id=_ROOM, binding_id=uuid.uuid4(), rearm_silence=True)
    sinks: list[list[Any]] = []

    async def _wakeup_binding(chatroom_id: uuid.UUID, agent_id: uuid.UUID, *, effect_sink: list[Any]):
        sinks.append(effect_sink)
        return WakeupBinding(room_id=chatroom_id, binding_id=effect.binding_id, effect_sink=effect_sink)

    engine._wakeup_binding = _wakeup_binding

    # (effects, commits so far) per application: the second element is what makes
    # "after the commit" an assertion rather than an assumption.
    applied: list[tuple[list[Any], int]] = []

    async def _apply(effects: list[Any]) -> None:
        applied.append((list(effects), engine._db.commits))

    monkeypatch.setattr(orch_facade.OrchestrationFacade, "apply_room_wakeup_effects", staticmethod(_apply))

    async def _stream(**kw: Any) -> ToolLoopOutcome:
        # Stands in for the model calling `update_wakeup` mid-turn.
        (sink,) = sinks
        sink.append(effect)
        if text == "raise":
            raise RuntimeError("provider fell over after the tool call")
        return ToolLoopOutcome(text=text, rounds=1)

    engine._stream_with_tools = _stream
    return engine, agent, effect, applied


async def test_a_reply_applies_the_timers_after_its_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, agent, effect, applied = _wire(monkeypatch, text="slowing down a little")

    result = await run_locked(engine, _ROOM, agent)

    assert result.status == "completed"
    assert applied == [([effect], engine._db.commits)]


async def test_an_empty_reply_still_applies_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tuning and then saying nothing is the teacher prompts' ordinary stage change."""
    engine, agent, effect, applied = _wire(monkeypatch, text="")

    result = await run_locked(engine, _ROOM, agent)

    assert result.status == "skipped"
    assert applied == [([effect], engine._db.commits)]


async def test_an_observer_turn_applies_them(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, agent, effect, applied = _wire(monkeypatch, text="a quiet note", role=ChatroomAgentRole.OBSERVER)

    class _ObservationService:
        def __init__(self, db: object) -> None:
            pass

        async def record(self, **kw: Any) -> SimpleNamespace:
            return SimpleNamespace(id=uuid.uuid4(), created_at=NOW)

    monkeypatch.setattr(te, "ObservationService", _ObservationService)

    async def _emit_observation_event(*a: Any, **k: Any) -> None:
        return None

    engine._emit_observation_event = _emit_observation_event

    result = await run_locked(engine, _ROOM, agent)

    assert result.status == "completed"
    assert applied == [([effect], engine._db.commits)]


async def test_a_failed_turn_applies_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure path rolls the binding write back, so its timers are not owed."""
    engine, agent, _effect, applied = _wire(monkeypatch, text="raise")

    result = await run_locked(engine, _ROOM, agent)

    assert result.status == "failed"
    assert engine._db.rollbacks >= 1
    assert applied == []
