"""Refresh of a binding's runtime wake-up layer ([R15.09]).

The interval is the designer's ``refresh_every_hours``, measured from the
binding's first actual runtime override; later tuning does not postpone it,
and refresh clears only that layer. Human room overrides and other rooms keep
their values (phase 2 AC-8, AC-9).
"""

from __future__ import annotations

import uuid

from contexts.orchestration.application.room_wakeup_service import apply_timer_effects
from contexts.orchestration.domain.room_wakeup import WakeupOverrides
from tests.unit.room_wakeup_fakes import make_harness

_CONFIG = {
    "triggers": {
        "every_n_messages": {"enabled": True, "n": 3},
        "silence_minutes": {"enabled": True, "t_minutes": 2},
    },
    "self_toggle": ["every_n_messages", "silence_minutes"],
    "refresh_every_hours": 6,
}


def _bound(monkeypatch, config=_CONFIG):
    h = make_harness(monkeypatch, config)
    room = uuid.uuid4()
    state = h.bindings.bind(h.agent.id, room)
    return h, room, state


async def _tune(h, room, state, **kw):
    return await h.svc.update_runtime(agent_id=h.agent.id, room_id=room, binding_id=state.binding_id, **kw)


async def _refresh(h, room) -> bool:
    """Whether the binding was refreshed; its timers are applied as the sweep would, after commit."""
    change = await h.svc.refresh_if_due(h.bindings.get(h.agent.id, room), at=h.clock.now)
    if change is None:
        return False
    await apply_timer_effects([change.effects])
    return True


async def test_refresh_is_skipped_inside_the_configured_window(monkeypatch) -> None:
    h, room, state = _bound(monkeypatch)
    await _tune(h, room, state, every_n_messages=9)
    h.clock.advance(hours=5, minutes=59)

    assert await _refresh(h, room) is False
    assert h.bindings.get(h.agent.id, room).runtime_overrides == {"every_n_messages": 9}


async def test_refresh_runs_outside_the_configured_window(monkeypatch) -> None:
    h, room, state = _bound(monkeypatch)
    await _tune(h, room, state, every_n_messages=9)
    h.clock.advance(hours=6)

    assert await _refresh(h, room) is True
    stored = h.bindings.get(h.agent.id, room)
    assert stored.runtime_overrides == {}
    assert stored.runtime_started_at is None
    assert h.audits[-1].action == "agent.wakeup_refreshed"
    assert h.audits[-1].metadata["previous"] == {"every_n_messages": 9}


async def test_later_tuning_does_not_postpone_the_refresh(monkeypatch) -> None:
    h, room, state = _bound(monkeypatch)
    await _tune(h, room, state, every_n_messages=9)
    started = h.bindings.get(h.agent.id, room).runtime_started_at
    h.clock.advance(hours=5)
    await _tune(h, room, state, silence_minutes=4)

    assert h.bindings.get(h.agent.id, room).runtime_started_at == started
    h.clock.advance(hours=1)
    assert await _refresh(h, room) is True


async def test_a_shortened_interval_can_make_a_binding_due_at_once(monkeypatch) -> None:
    h, room, state = _bound(monkeypatch)
    await _tune(h, room, state, every_n_messages=9)
    h.clock.advance(hours=2)
    h.agent.wakeup_config = {**_CONFIG, "refresh_every_hours": 1}

    assert await _refresh(h, room) is True


async def test_extreme_refresh_interval_does_not_overflow(monkeypatch) -> None:
    h, room, state = _bound(monkeypatch, {**_CONFIG, "refresh_every_hours": 24_000_000_000})
    await _tune(h, room, state, every_n_messages=9)

    assert await _refresh(h, room) is False


async def test_a_binding_without_a_runtime_layer_is_never_refreshed(monkeypatch) -> None:
    h, room, _state = _bound(monkeypatch)
    h.clock.advance(hours=100)

    assert await _refresh(h, room) is False
    assert h.bindings.writes == 0


async def test_refresh_restores_a_toggled_trigger(monkeypatch) -> None:
    """C AC-7 ([R15.09]): a toggle is runtime drift like `n`, undone at refresh."""
    h, room, state = _bound(monkeypatch)
    await _tune(h, room, state, silence_minutes_enabled=False)
    h.clock.advance(hours=6)
    h.timers.clear()

    await _refresh(h, room)

    current = await h.svc.load(agent_id=h.agent.id, room_id=room)
    assert current is not None
    assert current.effective.config.triggers.silence_minutes.enabled is True
    # Switching silence back on re-arms its clock instead of firing at once.
    assert h.timers == [("touch_silence", state.binding_id)]


async def test_refresh_keeps_the_human_room_overrides(monkeypatch) -> None:
    """AC-8: the teacher's room choice survives the agent's periodic reset."""
    h, room, state = _bound(monkeypatch)
    await h.svc.patch_room(
        agent_id=h.agent.id,
        room_id=room,
        binding_id=state.binding_id,
        expected_version=1,
        patch=WakeupOverrides(silence_minutes=10),
        actor_user_id=uuid.uuid4(),
    )
    await _tune(h, room, state, every_n_messages=9)
    h.clock.advance(hours=6)

    await _refresh(h, room)

    stored = h.bindings.get(h.agent.id, room)
    assert stored.room_overrides == {"silence_minutes": 10}
    assert stored.runtime_overrides == {}
