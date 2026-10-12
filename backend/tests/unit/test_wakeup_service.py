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


def _make_service(
    *, agent: SimpleNamespace, room_members: list[uuid.UUID], composing: bool = False
) -> WakeupService:
    svc = WakeupService.__new__(WakeupService)
    svc._db = None  # type: ignore[attr-defined]
    svc._agents_facade = SimpleNamespace(get_agent=_async_return(agent))  # type: ignore[attr-defined]
    svc._presence = SimpleNamespace(  # type: ignore[attr-defined]
        list_room=_async_return(room_members), composing_recently=_async_return(composing)
    )
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
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.pop_fire_due",
        _async_return(False),
    )


async def test_observer_every_n_fires_with_empty_presence_and_no_gated_bell(monkeypatch) -> None:
    agent = _agent(wakeup_config=_every_n_config(allow_self_open=False))
    svc = _make_service(agent=agent, room_members=[])
    _stub_every_n_state(monkeypatch)
    bells: list[uuid.UUID] = []

    async def _gated(a, room_id):
        bells.append(a.id)

    svc._notify_wakeup_gated = _gated  # type: ignore[method-assign]

    # Agent-authored: a user-authored message skips the gate for every role
    # (APP-3), which would make this pass without exercising the O-2 exemption.
    woken = await svc.on_message_created(
        room_id=uuid.uuid4(),
        sender_is_user=False,
        sender_agent_id=uuid.uuid4(),
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

    # Agent-authored for the same reason as above: nothing has demonstrated a
    # human is watching, which is the only case the gate still covers.
    woken = await svc.on_message_created(
        room_id=uuid.uuid4(),
        sender_is_user=False,
        sender_agent_id=uuid.uuid4(),
        agent_ids=[agent.id],
    )

    assert woken == []
    assert bells == [agent.id]


# ---------------------------------------------------------------------------
# Counting mode and deferred firings ([R15.01], [R15.01a])
# ---------------------------------------------------------------------------


def _counting_state(monkeypatch) -> dict:
    """An in-memory stand-in for the counter and the due flag."""
    state: dict = {"count": 0, "due": False}

    async def _incr(*_a, **_k):
        state["count"] += 1
        return state["count"]

    async def _set_due(*_a, **_k):
        state["due"] = True

    async def _pop_due(*_a, **_k):
        was, state["due"] = state["due"], False
        return was

    prefix = "contexts.orchestration.application.wakeup_service.wakeup_state."
    monkeypatch.setattr(prefix + "touch_silence_timestamp", _async_return(None))
    monkeypatch.setattr(prefix + "reset_autostop", _async_return(None))
    monkeypatch.setattr(prefix + "increment_message_count", _incr)
    monkeypatch.setattr(prefix + "set_fire_due", _set_due)
    monkeypatch.setattr(prefix + "pop_fire_due", _pop_due)
    return state


def _counting_agent(*, n: int, count: str | None) -> SimpleNamespace:
    trigger: dict = {"enabled": True, "n": n}
    if count is not None:
        trigger["count"] = count
    return _agent(wakeup_config={"triggers": {"every_n_messages": trigger}, "allow_self_open": True})


async def _send(svc, agent, *, human: bool | None = None, user: bool = True, author=None) -> bool:
    kwargs = {} if human is None else {"sender_is_human": human}
    woken = await svc.on_message_created(
        room_id=uuid.uuid4(),
        sender_is_user=user,
        sender_agent_id=author,
        agent_ids=[agent.id],
        **kwargs,
    )
    return agent.id in woken


async def test_human_count_ignores_agent_messages(monkeypatch) -> None:
    """C AC-1: three human messages fire, however many agent messages fall between."""
    agent = _counting_agent(n=3, count="human")
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    _counting_state(monkeypatch)
    peer = uuid.uuid4()

    fired = [
        await _send(svc, agent),
        await _send(svc, agent, user=False, author=peer),
        await _send(svc, agent, user=False, author=peer),
        await _send(svc, agent),
        await _send(svc, agent, user=False, author=agent.id),
        await _send(svc, agent),
    ]

    assert fired == [False, False, False, False, False, True]


async def test_human_count_ignores_a_released_observation(monkeypatch) -> None:
    """C AC-2: it resets autostop like a user message but an agent wrote it."""
    agent = _counting_agent(n=1, count="human")
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    state = _counting_state(monkeypatch)

    assert await _send(svc, agent, user=True, human=False) is False
    assert state["count"] == 0


async def test_all_count_is_unchanged_when_the_key_is_absent(monkeypatch) -> None:
    """C AC-3: agents written before the option keep counting every message."""
    agent = _counting_agent(n=2, count=None)
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    _counting_state(monkeypatch)

    fired = [await _send(svc, agent), await _send(svc, agent, user=False, author=uuid.uuid4())]

    assert fired == [False, True]


async def test_a_firing_on_the_agents_own_message_is_deferred_not_dropped(monkeypatch) -> None:
    """C AC-3 / [R15.01a]: before, this firing was silently lost."""
    agent = _counting_agent(n=2, count="all")
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    state = _counting_state(monkeypatch)

    first = await _send(svc, agent)
    own = await _send(svc, agent, user=False, author=agent.id)
    assert (first, own, state["due"]) == (False, False, True)

    assert await _send(svc, agent) is True
    assert state["due"] is False


# ---------------------------------------------------------------------------
# Composing is room activity ([R15.02])
# ---------------------------------------------------------------------------


async def test_recent_typing_rearms_instead_of_firing(monkeypatch) -> None:
    """C AC-10a: a student pausing mid-sentence is not a lull."""
    agent = _agent(wakeup_config=_silence_config(allow_self_open=True))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()], composing=True)
    _stub_stale_but_ready_silence_state(monkeypatch)
    touched: list[bool] = []

    async def _touch(*_a, **_k):
        touched.append(True)

    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.touch_silence_timestamp", _touch
    )

    assert await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4()) is False
    assert touched == [True]


async def test_an_observer_also_waits_for_composing(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=True))
    svc = _make_service(agent=agent, room_members=[], composing=True)
    _stub_stale_but_ready_silence_state(monkeypatch)
    monkeypatch.setattr(
        "contexts.orchestration.application.wakeup_service.wakeup_state.touch_silence_timestamp",
        _async_return(None),
    )

    assert (
        await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4(), is_observer=True) is False
    )


async def test_without_recent_typing_the_lull_fires(monkeypatch) -> None:
    agent = _agent(wakeup_config=_silence_config(allow_self_open=True))
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()], composing=False)
    _stub_stale_but_ready_silence_state(monkeypatch)

    assert await svc.evaluate_silence_trigger(agent_id=agent.id, room_id=uuid.uuid4()) is True


# ---------------------------------------------------------------------------
# Parsing the new keys
# ---------------------------------------------------------------------------


def test_unknown_count_and_toggle_values_resolve_to_the_safe_default() -> None:
    cfg = WakeupConfig.from_dict(
        {
            "triggers": {"every_n_messages": {"enabled": True, "n": 2, "count": "robots"}},
            "self_toggle": ["silence_minutes", "call_only", 7],
        }
    )

    assert cfg.triggers.every_n_messages.count == "all"
    assert cfg.self_toggle == frozenset({"silence_minutes"})
    assert cfg.to_dict()["self_toggle"] == ["silence_minutes"]
    assert cfg.to_dict()["triggers"]["every_n_messages"]["count"] == "all"


async def test_a_deferred_firing_never_fires_twice(monkeypatch) -> None:
    """A message that is due anyway also consumes a pending deferral; before, the
    flag survived it and the next message fired a second time."""
    agent = _counting_agent(n=2, count="all")
    svc = _make_service(agent=agent, room_members=[uuid.uuid4()])
    state = _counting_state(monkeypatch)

    fired = [
        await _send(svc, agent),  # 1
        await _send(svc, agent, user=False, author=agent.id),  # 2: own, deferred
        await _send(svc, agent, user=False, author=agent.id),  # 3: own
        await _send(svc, agent),  # 4: due anyway, and takes the deferral with it
        await _send(svc, agent),  # 5
    ]

    assert fired == [False, False, False, True, False]
    assert state["due"] is False
