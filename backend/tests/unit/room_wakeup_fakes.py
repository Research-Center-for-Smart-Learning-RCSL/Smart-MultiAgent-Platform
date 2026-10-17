"""In-memory stand-ins for room wake-up tests ([R15.09], [R15.25]).

``FakeBindings`` replaces the conversation facade's binding-layer reads and its
CAS write, keyed the way the table is: one row per (room, agent), a fresh
``binding_id`` per bind, and a version that every successful write advances.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import contexts.orchestration.application.room_wakeup_service as rws
from contexts.conversation.domain.models import BindingWakeupState, ChatroomAgentRole
from contexts.orchestration.application.room_wakeup_service import RoomWakeupService


class FakeBindings:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], BindingWakeupState] = {}
        self.writes = 0
        # Set to make the next CAS lose to a concurrent writer once.
        self.lose_next_write = False

    def bind(
        self, agent_id: uuid.UUID, room_id: uuid.UUID, *, role: ChatroomAgentRole = ChatroomAgentRole.NORMAL
    ) -> BindingWakeupState:
        state = BindingWakeupState(
            chatroom_id=room_id,
            agent_id=agent_id,
            binding_id=uuid.uuid4(),
            role=role,
            version=1,
            room_overrides={},
            runtime_overrides={},
            runtime_started_at=None,
        )
        self.rows[(room_id, agent_id)] = state
        return state

    def unbind(self, agent_id: uuid.UUID, room_id: uuid.UUID) -> None:
        del self.rows[(room_id, agent_id)]

    def get(self, agent_id: uuid.UUID, room_id: uuid.UUID) -> BindingWakeupState:
        return self.rows[(room_id, agent_id)]

    async def binding_wakeup_state(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> Any:
        return self.rows.get((chatroom_id, agent_id))

    async def write_binding_wakeup_layers(
        self,
        *,
        binding_id: uuid.UUID,
        expected_version: int,
        room_overrides: dict[str, Any],
        runtime_overrides: dict[str, Any],
        runtime_started_at: datetime | None,
    ) -> BindingWakeupState | None:
        key = next((k for k, v in self.rows.items() if v.binding_id == binding_id), None)
        if key is None:
            return None
        row = self.rows[key]
        if self.lose_next_write:
            self.lose_next_write = False
            self.rows[key] = row = replace(row, version=row.version + 1)
        if row.version != expected_version:
            return None
        self.writes += 1
        self.rows[key] = replace(
            row,
            version=expected_version + 1,
            room_overrides=dict(room_overrides),
            runtime_overrides=dict(runtime_overrides),
            runtime_started_at=runtime_started_at,
        )
        return self.rows[key]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def wire_wakeup_service(svc: Any, *agents: Any) -> None:
    """Point a ``WakeupService.__new__`` instance at in-memory bindings for ``agents``.

    Each agent gets one normal binding with empty layers in whatever room is
    asked about, so the designer's config is the effective one.
    """
    by_id = {a.id: a for a in agents}

    async def _get_agent(agent_id: uuid.UUID) -> Any:
        return by_id.get(agent_id)

    async def _states(room_id: uuid.UUID) -> list[BindingWakeupState]:
        return [
            BindingWakeupState(
                chatroom_id=room_id,
                agent_id=a.id,
                binding_id=a.id,
                role=ChatroomAgentRole.NORMAL,
                version=1,
                room_overrides={},
                runtime_overrides={},
                runtime_started_at=None,
            )
            for a in agents
        ]

    room_wakeup = RoomWakeupService.__new__(RoomWakeupService)
    room_wakeup._agents = SimpleNamespace(get_agent=_get_agent)  # type: ignore[assignment]
    svc._room_wakeup = room_wakeup
    svc._conversation = SimpleNamespace(room_binding_wakeup_states=_states)


def make_agent(wakeup_config: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), deleted_at=None, version=1, wakeup_config=wakeup_config)


class Harness(SimpleNamespace):
    svc: RoomWakeupService
    bindings: FakeBindings
    agent: SimpleNamespace
    clock: Clock
    audits: list[Any]
    timers: list[tuple[str, uuid.UUID]]


def make_harness(monkeypatch: pytest.MonkeyPatch, wakeup_config: dict[str, Any]) -> Harness:
    agent = make_agent(wakeup_config)
    bindings = FakeBindings()
    clock = Clock()
    audits: list[Any] = []
    timers: list[tuple[str, uuid.UUID]] = []

    async def _get_agent(agent_id: uuid.UUID) -> Any:
        return agent if agent_id == agent.id else None

    async def _emit(_db: Any, event: Any, *, isolated: bool = False) -> bool:
        audits.append(event)
        return True

    async def _pop(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> bool:
        timers.append(("pop_fire_due", binding_id))
        return False

    async def _touch(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> None:
        timers.append(("touch_silence", binding_id))

    async def _restart(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> None:
        timers.append(("restart_count", binding_id))

    monkeypatch.setattr(rws.audit, "emit", _emit)
    monkeypatch.setattr(rws.wakeup_state, "pop_fire_due", _pop)
    monkeypatch.setattr(rws.wakeup_state, "touch_silence_timestamp", _touch)
    monkeypatch.setattr(rws.wakeup_state, "restart_message_count", _restart)
    monkeypatch.setattr(rws, "now", lambda: clock.now)

    svc = RoomWakeupService.__new__(RoomWakeupService)
    svc._db = None  # type: ignore[assignment]
    svc._agents = SimpleNamespace(get_agent=_get_agent)  # type: ignore[assignment]
    svc._conversation = bindings  # type: ignore[assignment]
    return Harness(svc=svc, bindings=bindings, agent=agent, clock=clock, audits=audits, timers=timers)
