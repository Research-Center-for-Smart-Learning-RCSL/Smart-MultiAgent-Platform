"""WakeupService.evaluate_silence_trigger -- live-roster re-check (B1 AC-2).

R15.05b: the empty-roster re-check must run unconditionally, not only when
``allow_self_open`` is false. ``is_silence_active`` can go stale between an
unclean disconnect and the retention scrub reconciling it (see
`_scrub_stale_presence` in app/workers/tasks/retention.py) -- a self-opening
silence agent must not trust that flag alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from contexts.orchestration.application.wakeup_service import WakeupService


def _async_return(value):
    async def _f(*_a, **_k):
        return value

    return _f


def _agent(*, wakeup_config: dict) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), deleted_at=None, wakeup_config=wakeup_config)


def _silence_config(*, allow_self_open: bool) -> dict:
    return {
        "triggers": {"silence_minutes": {"enabled": True, "t_minutes": 2, "autostop_rounds": 5}},
        "allow_self_open": allow_self_open,
    }


def _make_service(*, agent: SimpleNamespace, room_members: list[uuid.UUID]) -> WakeupService:
    svc = WakeupService.__new__(WakeupService)
    svc._db = None  # type: ignore[attr-defined]
    svc._agents_facade = SimpleNamespace(get_agent=_async_return(agent))  # type: ignore[attr-defined]
    svc._presence = SimpleNamespace(list_room=_async_return(room_members))  # type: ignore[attr-defined]
    return svc


def _stub_stale_but_ready_silence_state(monkeypatch, *, autostop_count: int = 0) -> None:
    """Everything short of the roster re-check says "fire": silence has been
    active long enough and autostop hasn't tripped."""
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.is_silence_active",
        _async_return(True),
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_silence_timestamp",
        _async_return(datetime.now(UTC) - timedelta(minutes=10)),
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_autostop_count",
        _async_return(autostop_count),
    )


async def test_allow_self_open_true_does_not_fire_into_empty_room(monkeypatch) -> None:
    """Before the fix, allow_self_open=true skipped the roster re-check
    entirely, so a stale is_silence_active flag alone fired the trigger."""
    agent = _agent(wakeup_config=_silence_config(allow_self_open=True))
    svc = _make_service(agent=agent, room_members=[])
    _stub_stale_but_ready_silence_state(monkeypatch)

    fired = await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4())

    assert fired is False


async def test_allow_self_open_true_fires_with_a_live_roster(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=True))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    _stub_stale_but_ready_silence_state(monkeypatch)

    fired = await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4())

    assert fired is True


async def test_allow_self_open_false_still_does_not_fire_into_empty_room(monkeypatch) -> None:
    """Pre-existing behaviour (not just the allow_self_open=true path) stays intact."""
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[])
    _stub_stale_but_ready_silence_state(monkeypatch)

    fired = await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4())

    assert fired is False


# --------------------------------------------------------------------------- #
# O-2 (F-2): observer bindings are exempt from the empty-room presence gates —
# observer output is out-of-band (R28.04), so "don't fire into an empty room"
# does not apply, and the gated-wakeup bell must never name an observer
# (R28.09/R28.10).
# --------------------------------------------------------------------------- #


async def test_observer_silence_fires_in_empty_room_even_when_paused(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[])
    _stub_stale_but_ready_silence_state(monkeypatch)
    # The role-blind presence pause set the flag inactive when the room
    # emptied — an observer must fire regardless.
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.is_silence_active",
        _async_return(False),
    )

    fired = await svc.evaluate_silence_trigger(
        agent_id=agent.id, room_id=uuid.uuid4(), is_observer=True
    )

    assert fired is True


async def test_normal_silence_still_respects_paused_flag(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=True))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    _stub_stale_but_ready_silence_state(monkeypatch)
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.is_silence_active",
        _async_return(False),
    )

    fired = await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4())

    assert fired is False


def _every_n_config(*, allow_self_open: bool = False) -> dict:
    return {
        "triggers": {"every_n_messages": {"enabled": True, "n": 1}},
        "allow_self_open": allow_self_open,
    }


def _stub_every_n_state(monkeypatch) -> None:
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


async def test_observer_every_n_fires_with_empty_presence_and_no_gated_bell(monkeypatch) -> None:
    agent = _agent(wakeup_config=_every_n_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[])
    _stub_every_n_state(monkeypatch)
    bells: list[uuid.UUID] = []

    async def _gated(a, room_id):
        bells.append(a.id)

    svc._notify_wakeup_gated = _gated  # type: ignore[method-assign]

    woken = await svc.on_message_created(
        room_id=uuid.uuid4(),
        sender_is_user=True,
        agent_ids=[agent.id],
        observer_agent_ids={agent.id},
    )

    assert woken == [agent.id]
    assert bells == []


async def test_normal_every_n_still_gated_with_bell_when_room_empty(monkeypatch) -> None:
    agent = _agent(wakeup_config=_every_n_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[])
    _stub_every_n_state(monkeypatch)
    bells: list[uuid.UUID] = []

    async def _gated(a, room_id):
        bells.append(a.id)

    svc._notify_wakeup_gated = _gated  # type: ignore[method-assign]

    woken = await svc.on_message_created(
        room_id=uuid.uuid4(),
        sender_is_user=True,
        agent_ids=[agent.id],
    )

    assert woken == []
    assert bells == [agent.id]
