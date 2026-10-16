"""Human room wake-up settings and their timer side effects ([R15.09], [R15.25]).

Phase 2 AC-8 (patch only supplied fields; reset scopes), AC-9 (timers and
refresh start), AC-10 (version conflicts and binding incarnations).
"""

from __future__ import annotations

import uuid

import pytest

from contexts.orchestration.application.room_wakeup_service import apply_timer_effects
from contexts.orchestration.domain.errors import RoomWakeupBindingGone, RoomWakeupVersionConflict
from contexts.orchestration.domain.room_wakeup import WakeupOverrides
from tests.unit.room_wakeup_fakes import make_harness

_CONFIG = {
    "triggers": {
        "every_n_messages": {"enabled": True, "n": 3},
        "silence_minutes": {"enabled": False, "t_minutes": 2, "autostop_rounds": 5},
    },
    "self_toggle": ["every_n_messages", "silence_minutes"],
}
_TEACHER = uuid.uuid4()


def _bound(monkeypatch):
    h = make_harness(monkeypatch, _CONFIG)
    room = uuid.uuid4()
    return h, room, h.bindings.bind(h.agent.id, room)


async def _patch(h, room, state, patch: WakeupOverrides, *, version: int | None = None):
    """The route's sequence: write, commit, then apply the timers the write owes."""
    return await _committed(await _patch_uncommitted(h, room, state, patch, version=version))


async def _committed(change):
    await apply_timer_effects([change.effects])
    return change.wakeup


async def _patch_uncommitted(h, room, state, patch: WakeupOverrides, *, version: int | None = None):
    current = h.bindings.get(h.agent.id, room)
    return await h.svc.patch_room(
        agent_id=h.agent.id,
        room_id=room,
        binding_id=state.binding_id,
        expected_version=current.version if version is None else version,
        patch=patch,
        actor_user_id=_TEACHER,
    )


async def _reset(h, room, state, scope):
    return await _committed(await _reset_uncommitted(h, room, state, scope))


async def _reset_uncommitted(h, room, state, scope):
    return await h.svc.reset(
        agent_id=h.agent.id,
        room_id=room,
        binding_id=state.binding_id,
        expected_version=h.bindings.get(h.agent.id, room).version,
        scope=scope,
        actor_user_id=_TEACHER,
    )


async def _tune(h, room, state, **kw):
    tuning = await _tune_uncommitted(h, room, state, **kw)
    await apply_timer_effects([tuning.effects])
    return tuning.config


async def _tune_uncommitted(h, room, state, **kw):
    return await h.svc.update_runtime(agent_id=h.agent.id, room_id=room, binding_id=state.binding_id, **kw)


class TestPatch:
    async def test_only_supplied_fields_change_and_the_agent_row_does_not(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)
        designer = dict(h.agent.wakeup_config)
        await _tune(h, room, state, every_n_messages=7, silence_minutes=4)

        updated = await _patch(h, room, state, WakeupOverrides(every_n_messages=5))

        # The human value replaces the agent's for that field only.
        assert updated.room.to_dict() == {"every_n_messages": 5}
        assert updated.runtime.to_dict() == {"silence_minutes": 4}
        assert updated.effective.sources["every_n_messages"] == "room"
        assert updated.effective.sources["silence_minutes"] == "runtime"
        assert h.agent.wakeup_config == designer
        assert h.audits[-1].action == "chatroom.agent_wakeup_updated"
        assert h.audits[-1].metadata["fields"] == ["every_n_messages"]

    async def test_a_human_can_pause_both_triggers(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)

        updated = await _patch(
            h, room, state, WakeupOverrides(every_n_messages_enabled=False, silence_minutes_enabled=False)
        )

        assert updated.effective.config.triggers.every_n_messages.enabled is False
        assert updated.effective.config.triggers.silence_minutes.enabled is False

    async def test_a_stale_version_is_a_conflict(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)
        await _patch(h, room, state, WakeupOverrides(every_n_messages=5))

        with pytest.raises(RoomWakeupVersionConflict):
            await _patch(h, room, state, WakeupOverrides(every_n_messages=6), version=1)
        assert h.bindings.get(h.agent.id, room).room_overrides == {"every_n_messages": 5}

    async def test_a_rebound_binding_is_a_new_incarnation(self, monkeypatch) -> None:
        h, room, old = _bound(monkeypatch)
        await _patch(h, room, old, WakeupOverrides(every_n_messages=5))
        h.bindings.unbind(h.agent.id, room)
        fresh = h.bindings.bind(h.agent.id, room)

        with pytest.raises(RoomWakeupBindingGone):
            await _patch(h, room, old, WakeupOverrides(every_n_messages=6), version=2)
        current = await h.svc.load(agent_id=h.agent.id, room_id=room)
        assert current is not None
        assert current.state.binding_id == fresh.binding_id
        assert current.room.is_empty()

    async def test_saving_the_stored_value_again_writes_nothing(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)
        await _patch(h, room, state, WakeupOverrides(every_n_messages=5))
        writes, audits = h.bindings.writes, len(h.audits)

        await _patch(h, room, state, WakeupOverrides(every_n_messages=5))

        assert h.bindings.writes == writes
        assert len(h.audits) == audits


class TestReset:
    async def test_runtime_reset_keeps_the_room_layer(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)
        await _patch(h, room, state, WakeupOverrides(silence_minutes=9))
        await _tune(h, room, state, every_n_messages=7)

        updated = await _reset(h, room, state, "runtime")

        assert updated.room.to_dict() == {"silence_minutes": 9}
        assert updated.runtime.is_empty()
        assert updated.state.runtime_started_at is None

    async def test_room_reset_returns_to_the_current_defaults(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)
        await _patch(h, room, state, WakeupOverrides(silence_minutes=9))
        await _tune(h, room, state, every_n_messages=7)

        updated = await _reset(h, room, state, "room")

        assert updated.room.is_empty()
        assert updated.runtime.is_empty()
        assert set(updated.effective.sources.values()) == {"designer"}


class TestTimersWaitForTheCommit:
    """D-14 (FU-9): a write touches no Redis timer itself. The caller applies the
    returned effects after the transaction holding the write commits, so a rollback
    leaves the timers as they were."""

    async def test_a_human_write_touches_no_timer_until_applied(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)

        dropped = await _patch_uncommitted(h, room, state, WakeupOverrides(every_n_messages_enabled=False))
        reset = await _reset_uncommitted(h, room, state, "room")

        assert h.timers == []
        assert dropped.effects is not None
        assert dropped.effects.drop_deferred_fire
        assert reset.effects is None  # the reset re-enables only the message trigger

        await apply_timer_effects([dropped.effects, reset.effects])
        assert h.timers == [("pop_fire_due", state.binding_id)]

    async def test_a_tool_write_touches_no_timer_until_applied(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)

        tuning = await _tune_uncommitted(h, room, state, silence_minutes_enabled=True)

        assert h.timers == []
        assert tuning.effects is not None
        assert tuning.effects.rearm_silence

    async def test_a_no_op_owes_no_timer(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)

        change = await _patch_uncommitted(h, room, state, WakeupOverrides(every_n_messages=3))

        assert change.effects is None


class TestTimers:
    async def test_switching_the_message_trigger_off_drops_its_deferred_firing(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)

        await _patch(h, room, state, WakeupOverrides(every_n_messages_enabled=False))

        assert h.timers == [("pop_fire_due", state.binding_id)]

    async def test_enabling_silence_rearms_its_clock(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)

        await _patch(h, room, state, WakeupOverrides(silence_minutes_enabled=True))
        await _patch(h, room, state, WakeupOverrides(silence_minutes=6))

        assert h.timers == [("touch_silence", state.binding_id), ("touch_silence", state.binding_id)]

    async def test_a_change_to_n_touches_no_timer(self, monkeypatch) -> None:
        """No configuration change resets autostop or the message count."""
        h, room, state = _bound(monkeypatch)

        await _patch(h, room, state, WakeupOverrides(every_n_messages=8))
        await _tune(h, room, state, every_n_messages=4)

        assert h.timers == []

    async def test_the_first_runtime_override_starts_the_refresh_clock(self, monkeypatch) -> None:
        h, room, state = _bound(monkeypatch)
        await _patch(h, room, state, WakeupOverrides(every_n_messages=8))
        assert h.bindings.get(h.agent.id, room).runtime_started_at is None

        await _tune(h, room, state, silence_minutes=5)

        assert h.bindings.get(h.agent.id, room).runtime_started_at == h.clock.now
