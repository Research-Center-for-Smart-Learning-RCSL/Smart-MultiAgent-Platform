"""WakeupService.evaluate_silence_trigger -- authoritative live-roster checks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from contexts.orchestration.application.wakeup_service import WakeupService
from contexts.orchestration.domain.models import (
    N_MAX,
    N_MIN,
    T_MINUTES_MAX,
    T_MINUTES_MIN,
    WakeupConfig,
)


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
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_silence_timestamp",
        _async_return(datetime.now(UTC) - timedelta(minutes=10)),
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_autostop_count",
        _async_return(autostop_count),
    )


async def test_a_recently_re_armed_clock_does_not_fire(monkeypatch) -> None:
    """The behavioural half of the activity-submission fix ([R15.02]).

    An activity submission re-arms the silence timestamp via
    ``triggers.evaluate_room_activity`` -> ``on_users_present`` ->
    ``touch_silence_timestamp``. What that has to buy is this: while submissions
    keep arriving inside the window, the trigger must not fire. Before the fix
    nothing touched the timestamp on submit, so a class quietly filling in a
    worksheet looked identical to an empty room and the peer agent barged in.

    The room roster is non-empty on purpose -- students hold live sockets while
    they write, so the presence gate passes and the timestamp is the only thing
    standing between them and an interruption.
    """
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_silence_timestamp",
        _async_return(datetime.now(UTC) - timedelta(seconds=30)),  # inside t_minutes=2
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_autostop_count",
        _async_return(0),
    )

    fired = await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4())

    assert fired is False


async def test_a_genuine_lull_past_the_window_still_fires(monkeypatch) -> None:
    """The converse, so the test above cannot pass by suppressing the trigger
    outright: once nothing has re-armed the clock for longer than the window, the
    agent does speak."""
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    _stub_stale_but_ready_silence_state(monkeypatch)

    fired = await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4())

    assert fired is True


async def test_allow_self_open_true_does_not_fire_into_empty_room(monkeypatch) -> None:
    """Self-opening agents still require a live user for silence wake-ups."""
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


async def test_observer_silence_fires_in_empty_room(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[])
    _stub_stale_but_ready_silence_state(monkeypatch)
    # The role-blind presence pause set the flag inactive when the room
    # emptied — an observer must fire regardless.
    fired = await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4(), is_observer=True)

    assert fired is True


async def test_silence_seeds_its_clock_for_a_binding_created_after_the_join_edge(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    room_id = uuid.uuid4()
    timestamps = [None, datetime.now(UTC) - timedelta(minutes=10)]
    touched: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def _get_timestamp(*_args):
        return timestamps.pop(0)

    async def _touch(agent_id, touched_room_id):
        touched.append((agent_id, touched_room_id))

    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_silence_timestamp",
        _get_timestamp,
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.touch_silence_timestamp",
        _touch,
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_autostop_count",
        _async_return(0),
    )

    assert not await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=room_id)
    assert touched == [(agent.id, room_id)]
    assert await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=room_id)


async def test_join_still_rearms_the_silence_clock(monkeypatch) -> None:
    """T-5: after the C2 rename (2026-07-27-wakeup-sweep-failure-isolation),
    `on_users_present` -- the surviving half of the retired
    `on_presence_changed` hook -- must still re-arm the silence clock for
    every bound agent on the join edge."""
    svc = WakeupService.__new__(WakeupService)
    svc._db = None  # type: ignore[attr-defined]

    touched: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def _touch(agent_id, room_id):
        touched.append((agent_id, room_id))

    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.touch_silence_timestamp",
        _touch,
    )

    room_id = uuid.uuid4()
    agent_ids = [uuid.uuid4(), uuid.uuid4()]
    await svc.on_users_present(room_id=room_id, agent_ids=agent_ids)

    assert touched == [(agent_ids[0], room_id), (agent_ids[1], room_id)]


async def test_normal_silence_follows_the_live_roster_not_the_cached_flag(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    _stub_stale_but_ready_silence_state(monkeypatch)

    assert await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4())


async def test_observer_silence_uses_observer_autostop_cap(monkeypatch) -> None:
    """O-3 (P-1): the silence evaluator applies observer_autostop_rounds
    (default 50) to observer bindings instead of autostop_rounds."""
    agent = _agent(wakeup_config=_silence_config(allow_self_open=False))  # autostop_rounds=5
    svc = _make_service(agent=agent, room_members=[])
    room_id = uuid.uuid4()

    _stub_stale_but_ready_silence_state(monkeypatch, autostop_count=10)  # >= 5, < 50
    assert await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=room_id, is_observer=True)

    _stub_stale_but_ready_silence_state(monkeypatch, autostop_count=50)
    assert not await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=room_id, is_observer=True)


def test_wakeup_config_parses_observer_autostop_rounds() -> None:
    """O-3 (P-1): default 50, hard-capped at 100, round-trips through to_dict."""
    from contexts.orchestration.domain.models import WakeupConfig

    assert WakeupConfig.from_dict({}).triggers.silence_minutes.observer_autostop_rounds == 50
    cfg = WakeupConfig.from_dict({"triggers": {"silence_minutes": {"observer_autostop_rounds": 999}}})
    assert cfg.triggers.silence_minutes.observer_autostop_rounds == 100
    assert cfg.to_dict()["triggers"]["silence_minutes"]["observer_autostop_rounds"] == 100


def test_wakeup_config_clamps_every_numeric_field() -> None:
    low = WakeupConfig.from_dict(
        {
            "triggers": {
                "every_n_messages": {"n": 0},
                "silence_minutes": {
                    "t_minutes": 0,
                    "autostop_rounds": 0,
                    "observer_autostop_rounds": 0,
                    "autostop_max_default": 0,
                },
            },
            "refresh_every_hours": 0,
        }
    )
    low_sm = low.triggers.silence_minutes
    assert low.triggers.every_n_messages.n == 1
    assert low_sm.t_minutes == 1
    assert low_sm.autostop_rounds == 5
    assert low_sm.observer_autostop_rounds == 50
    assert low_sm.autostop_max_default == 100
    assert low.refresh_every_hours == 24

    negative = WakeupConfig.from_dict(
        {
            "triggers": {
                "every_n_messages": {"n": -3},
                "silence_minutes": {
                    "t_minutes": -3,
                    "autostop_rounds": -3,
                    "observer_autostop_rounds": -3,
                    "autostop_max_default": -3,
                },
            },
            "refresh_every_hours": -3,
        }
    )
    assert negative.to_dict() == low.to_dict()

    high = WakeupConfig.from_dict(
        {
            "triggers": {
                "every_n_messages": {"n": 5000},
                "silence_minutes": {
                    "t_minutes": 99999,
                    "autostop_rounds": 500,
                    "observer_autostop_rounds": 500,
                    "autostop_max_default": 500,
                },
            },
            "refresh_every_hours": 24_000_000_000,
        }
    )
    high_sm = high.triggers.silence_minutes
    assert high.triggers.every_n_messages.n == 1000
    assert high_sm.t_minutes == 1440
    assert high_sm.autostop_rounds == 100
    assert high_sm.observer_autostop_rounds == 100
    assert high_sm.autostop_max_default == 100
    assert high.refresh_every_hours == 24_000_000_000


def test_wakeup_config_resolves_wrong_typed_fields_to_defaults() -> None:
    """2026-07-27-wakeup-config-type-validation AC-2 (T-2): `from_dict` must never
    raise, and a wrong-typed numeric value resolves to the Q-2 default -- the same
    resolution `clamp`/`_default_below_one` already apply to an out-of-range value.
    `bool` (Q-7) is treated as wrong-typed too: `int(True) == 1` would otherwise
    silently become "wake on every message"."""
    bad_values: list[object] = [None, "abc", [], {}, True, False]
    for bad in bad_values:
        cfg = WakeupConfig.from_dict(
            {
                "triggers": {
                    "every_n_messages": {"n": bad},
                    "silence_minutes": {
                        "t_minutes": bad,
                        "autostop_rounds": bad,
                        "observer_autostop_rounds": bad,
                        "autostop_max_default": bad,
                    },
                },
                "refresh_every_hours": bad,
            }
        )
        sm = cfg.triggers.silence_minutes
        assert cfg.triggers.every_n_messages.n == 1
        assert sm.t_minutes == 1
        assert sm.autostop_rounds == 5
        assert sm.observer_autostop_rounds == 50
        assert sm.autostop_max_default == 100
        assert cfg.refresh_every_hours == 24
        # Round-trips through to_dict without reintroducing the bad value.
        assert cfg.to_dict()["triggers"]["every_n_messages"]["n"] == 1


def test_soft_bounds_tolerate_wrong_typed_values() -> None:
    """AC-3 (T-3): a wrong-typed soft bound resolves to `None` (absent, hard bounds
    apply) instead of raising later in `_clamp_n`/`_clamp_t`, and an inverted or
    out-of-hard-range bound never lets those helpers return a value outside
    [N_MIN, N_MAX] / [T_MINUTES_MIN, T_MINUTES_MAX] (prior dossier's FU-7)."""
    cfg = WakeupConfig.from_dict({"soft_bounds": {"n_min": "five", "n_max": None, "t_minutes_min": True}})
    assert cfg.soft_bounds is not None
    assert cfg.soft_bounds.n_min is None
    assert cfg.soft_bounds.n_max is None
    assert cfg.soft_bounds.t_minutes_min is None

    n = WakeupService._clamp_n(500, cfg.soft_bounds)
    assert N_MIN <= n <= N_MAX
    t = WakeupService._clamp_t(500, cfg.soft_bounds)
    assert T_MINUTES_MIN <= t <= T_MINUTES_MAX

    inverted = WakeupConfig.from_dict({"soft_bounds": {"n_min": 900, "n_max": 3}}).soft_bounds
    assert inverted is not None
    for value in (1, 500, 1000, 5000, -10):
        assert N_MIN <= WakeupService._clamp_n(value, inverted) <= N_MAX

    out_of_range = WakeupConfig.from_dict({"soft_bounds": {"n_min": 99999}}).soft_bounds
    assert out_of_range is not None
    assert out_of_range.n_min == N_MAX
    for value in (1, 500, 1000, 5000):
        assert N_MIN <= WakeupService._clamp_n(value, out_of_range) <= N_MAX


def test_from_dict_tolerates_a_non_dict_triggers_container() -> None:
    """F-1 (2026-07-28-wakeup-config-validation-and-patch-semantics): a truthy
    non-dict `triggers` (or any of its three sub-keys) must resolve to defaults,
    not raise `AttributeError` from `.get()` on a non-dict -- the same tolerance
    `soft_bounds` already had via its own `isinstance` guard."""
    assert WakeupConfig.from_dict({"triggers": "x"}) == WakeupConfig.from_dict({})
    assert WakeupConfig.from_dict({"triggers": {"every_n_messages": "x"}}) == WakeupConfig.from_dict({})
    assert WakeupConfig.from_dict({"triggers": {"silence_minutes": []}}) == WakeupConfig.from_dict({})
    assert WakeupConfig.from_dict({"triggers": {"call_only": 1}}) == WakeupConfig.from_dict({})


def test_from_dict_tolerates_a_non_bool_enabled_flag() -> None:
    """F-2: `bool("false") is True` in Python, so a wrong-typed `enabled` must
    resolve to a safe default (`False`) instead of being coerced with a bare
    `bool(...)`, which would silently enable what the caller meant to disable."""
    bad_values: list[object] = ["false", "0", [], {}, 1]
    for bad in bad_values:
        cfg = WakeupConfig.from_dict({"triggers": {"call_only": {"enabled": bad}}})
        assert cfg.triggers.call_only.enabled is False
    cfg = WakeupConfig.from_dict({"allow_self_open": "false"})
    assert cfg.allow_self_open is False
    # A genuine bool still round-trips correctly in both directions.
    assert (
        WakeupConfig.from_dict({"triggers": {"call_only": {"enabled": True}}}).triggers.call_only.enabled
        is True
    )


async def test_observer_zero_autostop_uses_the_parsed_default(monkeypatch) -> None:
    agent = _agent(
        wakeup_config={
            "triggers": {
                "silence_minutes": {
                    "enabled": True,
                    "t_minutes": 2,
                    "observer_autostop_rounds": 0,
                }
            }
        }
    )
    svc = _make_service(agent=agent, room_members=[])
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_silence_timestamp",
        _async_return(datetime.now(UTC) - timedelta(minutes=10)),
    )
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.get_autostop_count",
        _async_return(0),
    )

    assert await svc.evaluate_silence_trigger(
        agent_id=agent.id,
        room_id=uuid.uuid4(),
        is_observer=True,
    )


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
