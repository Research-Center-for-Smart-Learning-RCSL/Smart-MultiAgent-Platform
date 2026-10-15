"""The agent's ``update_wakeup`` tool, scoped to one room binding ([R15.06], [R15.06a]).

Before classroom facilitation phase 2 the tool rewrote ``Agent.wakeup_config``,
so a teacher agent tuning itself in one room changed every room it was bound
to and, through a later designer save, its designer's defaults. It now writes
only the calling binding's runtime layer
(``docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence``
AC-6, AC-7, AC-11).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.orchestration.domain.errors import (
    RoomWakeupBindingGone,
    WakeupToggleNotAllowed,
)
from tests.unit.room_wakeup_fakes import make_harness

_BOTH_ON: dict[str, Any] = {
    "triggers": {
        "every_n_messages": {"enabled": True, "n": 7},
        "silence_minutes": {"enabled": True, "t_minutes": 2},
    },
    "soft_bounds": {"n_min": 5, "n_max": 10},
    "self_toggle": ["every_n_messages", "silence_minutes"],
    "designer_note": "x",
}


def _bound(monkeypatch, config=_BOTH_ON):
    h = make_harness(monkeypatch, config)
    room = uuid.uuid4()
    state = h.bindings.bind(h.agent.id, room)
    return h, room, state


async def _tool(h, room, state, **kw):
    return await h.svc.update_runtime(agent_id=h.agent.id, room_id=room, binding_id=state.binding_id, **kw)


async def test_self_modification_preserves_designer_config_and_bounds(monkeypatch) -> None:
    h, room, state = _bound(monkeypatch)
    designer = dict(h.agent.wakeup_config)

    cfg = await _tool(h, room, state, every_n_messages=1)
    await _tool(h, room, state, every_n_messages=1)

    # Clamped to the soft floor, stored on the binding, never on the agent.
    assert cfg.triggers.every_n_messages.n == 5
    assert h.agent.wakeup_config == designer
    assert h.bindings.get(h.agent.id, room).runtime_overrides == {"every_n_messages": 5}
    assert [e.action for e in h.audits] == ["agent.wakeup_clamped", "agent.wakeup_clamped"]
    assert h.audits[0].metadata["binding_id"] == str(state.binding_id)


async def test_an_allowed_toggle_is_written_and_audited(monkeypatch) -> None:
    """C AC-4."""
    h, room, state = _bound(monkeypatch)

    cfg = await _tool(h, room, state, silence_minutes_enabled=False)

    assert cfg.triggers.silence_minutes.enabled is False
    assert h.bindings.get(h.agent.id, room).runtime_overrides == {"silence_minutes_enabled": False}
    (event,) = h.audits
    assert event.action == "agent.wakeup_toggled"
    assert event.metadata | {"room_id": None} == {
        "room_id": None,
        "binding_id": str(state.binding_id),
        "trigger": "silence_minutes",
        "enabled": False,
        "changed": True,
        "actor_agent_id": str(h.agent.id),
    }


async def test_an_unchanged_toggle_is_audited_but_writes_nothing(monkeypatch) -> None:
    """[R15.06a]: every accepted toggle is recorded; a no-op bumps no version."""
    h, room, state = _bound(monkeypatch)

    await _tool(h, room, state, silence_minutes_enabled=True)

    assert h.bindings.writes == 0
    assert h.bindings.get(h.agent.id, room).version == state.version
    assert [e.metadata["changed"] for e in h.audits] == [False]
    assert h.timers == []


async def test_a_toggle_outside_self_toggle_is_refused(monkeypatch) -> None:
    """C AC-4: the default, an empty list, keeps [R15.06]'s old behaviour exactly."""
    h, room, state = _bound(monkeypatch, {**_BOTH_ON, "self_toggle": []})

    with pytest.raises(WakeupToggleNotAllowed, match="not allowed to switch silence_minutes"):
        await _tool(h, room, state, silence_minutes_enabled=False)
    assert h.bindings.writes == 0


async def test_toggles_are_refused_while_call_only(monkeypatch) -> None:
    """C AC-5."""
    config = {**_BOTH_ON, "triggers": {**_BOTH_ON["triggers"], "call_only": {"enabled": True}}}
    h, room, state = _bound(monkeypatch, config)

    with pytest.raises(WakeupToggleNotAllowed, match="only answers when called"):
        await _tool(h, room, state, every_n_messages_enabled=False)


async def test_a_toggle_that_would_leave_every_trigger_off_is_refused(monkeypatch) -> None:
    """C AC-5: an injected "go quiet" must not make the agent permanently inert."""
    h, room, state = _bound(monkeypatch)
    await _tool(h, room, state, silence_minutes_enabled=False)

    with pytest.raises(WakeupToggleNotAllowed, match="turn off every trigger"):
        await _tool(h, room, state, every_n_messages_enabled=False)


async def test_the_tool_cannot_reach_the_grant_or_the_mode(monkeypatch) -> None:
    """C AC-6: only n, t_minutes and the two enabled flags are writable."""
    h, room, state = _bound(monkeypatch)

    await _tool(h, room, state, every_n_messages=6, silence_minutes=3)

    stored = h.bindings.get(h.agent.id, room)
    assert set(stored.runtime_overrides) == {"every_n_messages", "silence_minutes"}
    assert stored.room_overrides == {}


async def test_tuning_in_one_room_leaves_the_other_room_alone(monkeypatch) -> None:
    """AC-6: one teacher bound to two rooms."""
    h, room_a, state_a = _bound(monkeypatch)
    room_b = uuid.uuid4()
    state_b = h.bindings.bind(h.agent.id, room_b)

    await _tool(h, room_a, state_a, every_n_messages=9)

    assert h.bindings.get(h.agent.id, room_b) == state_b


async def test_a_rebound_agent_cannot_use_the_old_binding(monkeypatch) -> None:
    """AC-10/AC-11: the closure's binding id is checked on every call."""
    h, room, old = _bound(monkeypatch)
    h.bindings.unbind(h.agent.id, room)
    h.bindings.bind(h.agent.id, room)

    with pytest.raises(RoomWakeupBindingGone):
        await _tool(h, room, old, every_n_messages=6)
    assert h.bindings.writes == 0


async def test_a_lost_race_retries_from_the_original_request(monkeypatch) -> None:
    h, room, state = _bound(monkeypatch)
    h.bindings.lose_next_write = True

    cfg = await _tool(h, room, state, every_n_messages=8)

    assert cfg.triggers.every_n_messages.n == 8
    assert h.bindings.writes == 1


async def test_a_toggle_revoked_while_stored_stops_applying(monkeypatch) -> None:
    """AC-7: designer policy is current policy, whatever the binding holds."""
    h, room, state = _bound(monkeypatch)
    await _tool(h, room, state, silence_minutes_enabled=False)

    h.agent.wakeup_config = {**_BOTH_ON, "self_toggle": ["every_n_messages"]}
    current = await h.svc.load(agent_id=h.agent.id, room_id=room)

    assert current is not None
    assert current.effective.config.triggers.silence_minutes.enabled is True
    assert current.effective.ignored_runtime == {"silence_minutes_enabled"}
