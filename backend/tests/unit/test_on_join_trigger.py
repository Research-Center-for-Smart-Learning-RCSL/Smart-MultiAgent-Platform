"""The ``on_join`` wake-up trigger ([R15.26]; phase 3 AC-5 to AC-8).

Spec: ``docs/tasks/2026-10-20-crat-facilitation-phase3-late-joiners-and-activity-state/spec.md`` §6.2.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import contexts.orchestration.application.wakeup_service as ws_mod
from app.api.v1.agents import _validate_wakeup_config
from contexts.agents.application.runtime import turn_engine as te
from contexts.agents.application.runtime.tool_registry import _UPDATE_WAKEUP_SCHEMA
from contexts.conversation.domain.models import BindingWakeupState, ChatroomAgentRole
from contexts.orchestration.application.room_wakeup_service import RoomWakeupService
from contexts.orchestration.application.wakeup_service import WakeupService
from contexts.orchestration.domain.models import ON_JOIN_COALESCE_SECONDS, WakeupConfig
from contexts.orchestration.domain.room_wakeup import (
    RUNTIME_FIELDS,
    WakeupOverrides,
    resolve_room_wakeup,
)

_ON_JOIN = {"triggers": {"on_join": {"enabled": True}}}


class TestConfig:
    def test_parses_and_round_trips(self) -> None:
        cfg = WakeupConfig.from_dict(_ON_JOIN)

        assert cfg.triggers.on_join.enabled is True
        assert cfg.to_dict()["triggers"]["on_join"] == {"enabled": True}
        assert WakeupConfig.from_dict(cfg.to_dict()) == cfg

    def test_absent_or_wrong_typed_is_off(self) -> None:
        assert WakeupConfig().triggers.on_join.enabled is False
        assert (
            WakeupConfig.from_dict({"triggers": {"on_join": {"enabled": "true"}}}).triggers.on_join.enabled
            is False
        )

    def test_an_agent_with_only_on_join_is_not_inert(self) -> None:
        """AC-8 (backend half)."""
        assert WakeupConfig.from_dict(_ON_JOIN).is_inert() is False
        assert WakeupConfig().is_inert() is True


class TestDesignerValidation:
    def test_accepts_a_boolean(self) -> None:
        assert _validate_wakeup_config(_ON_JOIN) == _ON_JOIN

    @pytest.mark.parametrize(
        "on_join",
        [{"enabled": "yes"}, {"enabled": True, "window_s": 30}, [True], "on"],
    )
    def test_refuses_anything_else(self, on_join: Any) -> None:
        with pytest.raises(ValueError, match=r"on_join"):
            _validate_wakeup_config({"triggers": {"on_join": on_join}})


class TestResolution:
    def _resolve(self, defaults: dict, *, room=None, runtime=None, observer=False):
        return resolve_room_wakeup(
            WakeupConfig.from_dict(defaults),
            room or WakeupOverrides(),
            runtime or WakeupOverrides(),
            is_observer=observer,
        )

    def test_designer_default_then_room_override(self) -> None:
        eff = self._resolve(_ON_JOIN)
        assert eff.config.triggers.on_join.enabled is True
        assert eff.sources["on_join_enabled"] == "designer"

        eff = self._resolve(_ON_JOIN, room=WakeupOverrides(on_join_enabled=False))
        assert eff.config.triggers.on_join.enabled is False
        assert eff.sources["on_join_enabled"] == "room"

    def test_never_for_an_observer_binding(self) -> None:
        eff = self._resolve(_ON_JOIN, room=WakeupOverrides(on_join_enabled=True), observer=True)

        assert eff.config.triggers.on_join.enabled is False

    def test_the_agent_cannot_toggle_it(self) -> None:
        """AC-7: not a runtime field, not in the tool's schema."""
        assert "on_join_enabled" not in RUNTIME_FIELDS
        stored = WakeupOverrides.from_dict({"on_join_enabled": True}, allowed=RUNTIME_FIELDS)
        assert stored.is_empty()
        assert not any("on_join" in key for key in _UPDATE_WAKEUP_SCHEMA["properties"])
        assert _UPDATE_WAKEUP_SCHEMA["additionalProperties"] is False


def _state(room: uuid.UUID, agent_id: uuid.UUID, role=ChatroomAgentRole.NORMAL) -> BindingWakeupState:
    return BindingWakeupState(
        chatroom_id=room,
        agent_id=agent_id,
        binding_id=uuid.uuid4(),
        role=role,
        version=1,
        room_overrides={},
        runtime_overrides={},
        runtime_started_at=None,
    )


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, has_messages: bool = True) -> None:
        self.room = uuid.uuid4()
        self.creator = uuid.uuid4()
        self.agents: dict[uuid.UUID, SimpleNamespace] = {}
        self.states: list[BindingWakeupState] = []
        self.claimed: set[str] = set()
        self.has_messages = has_messages
        self.message_reads = 0

        async def _claim(agent_id, room_id, binding_id, window_s) -> bool:
            assert window_s == ON_JOIN_COALESCE_SECONDS
            key = f"{agent_id}:{room_id}:{binding_id}"
            if key in self.claimed:
                return False
            self.claimed.add(key)
            return True

        monkeypatch.setattr(ws_mod.wakeup_state, "claim_join_batch", _claim)

        async def _get_agent(agent_id):
            return self.agents.get(agent_id)

        async def _states(room_id):
            return list(self.states)

        async def _room(room_id):
            return SimpleNamespace(created_by_user_id=self.creator)

        async def _has_messages(room_id):
            self.message_reads += 1
            return self.has_messages

        room_wakeup = RoomWakeupService.__new__(RoomWakeupService)
        room_wakeup._agents = SimpleNamespace(get_agent=_get_agent)  # type: ignore[assignment]
        self.svc = WakeupService.__new__(WakeupService)
        self.svc._db = None  # type: ignore[assignment]
        self.svc._room_wakeup = room_wakeup
        self.svc._conversation = SimpleNamespace(  # type: ignore[assignment]
            room_binding_wakeup_states=_states, get_chatroom=_room, room_has_messages=_has_messages
        )

    def bind(self, config: dict, role=ChatroomAgentRole.NORMAL) -> BindingWakeupState:
        agent = SimpleNamespace(id=uuid.uuid4(), deleted_at=None, version=1, wakeup_config=config)
        self.agents[agent.id] = agent
        state = _state(self.room, agent.id, role)
        self.states.append(state)
        return state

    async def join(self, who: uuid.UUID | None = None) -> list:
        return await self.svc.on_participant_joined(room_id=self.room, principal_id=who or uuid.uuid4())


class TestWakeSelection:
    async def test_two_arrivals_in_one_window_share_one_wake(self, monkeypatch) -> None:
        """AC-5."""
        h = _Harness(monkeypatch)
        state = h.bind(_ON_JOIN)

        first = await h.join()
        second = await h.join()

        assert [(t.agent_id, t.binding_id) for t in first] == [(state.agent_id, state.binding_id)]
        assert second == []

    async def test_observer_call_only_and_disabled_bindings_are_not_woken(self, monkeypatch) -> None:
        """AC-5."""
        h = _Harness(monkeypatch)
        h.bind(_ON_JOIN, role=ChatroomAgentRole.OBSERVER)
        h.bind({"triggers": {"on_join": {"enabled": True}, "call_only": {"enabled": True}}})
        h.bind({"triggers": {"silence_minutes": {"enabled": True}}})

        assert await h.join() == []

    async def test_a_room_nobody_has_spoken_in_wakes_only_a_self_opening_agent(self, monkeypatch) -> None:
        """AC-5 / Q-9."""
        h = _Harness(monkeypatch, has_messages=False)
        h.bind(_ON_JOIN)
        opener = h.bind({**_ON_JOIN, "allow_self_open": True})

        targets = await h.join()

        assert [t.agent_id for t in targets] == [opener.agent_id]
        assert h.message_reads == 1

    async def test_the_room_creators_arrival_wakes_nothing(self, monkeypatch) -> None:
        """D-2: agents are never shown the creator's arrival ([R13.79])."""
        h = _Harness(monkeypatch)
        h.bind(_ON_JOIN)

        assert await h.join(h.creator) == []
        assert h.claimed == set()


class TestTurn:
    def test_the_time_line_names_the_reason(self) -> None:
        """AC-6."""
        assert "You were woken by a participant joining." in te._time_note(te._now(), [], "on_join")
