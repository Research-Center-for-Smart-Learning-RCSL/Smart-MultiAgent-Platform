"""Room-owned wake-up settings: three layers resolved into one config ([R15.09]).

A live room binding's effective wake-up behaviour is the agent designer's
defaults (``Agent.wakeup_config``), then the room's durable human overrides,
then the agent's own temporary runtime overrides for that exact binding. Only
the fields below can be overridden; designer-only policy (``self_toggle``,
``call_only``, ``allow_self_open``, bounds and the refresh interval) always
comes from the defaults, so no layer can relax it.

Pure: every caller that decides whether a binding wakes -- message evaluation,
the silence sweep, the worker's final gate, the settings read and the agent's
tool -- resolves through :func:`resolve_room_wakeup`, so they cannot disagree.
Each read validates the layers against the *current* defaults: a toggle the
designer has since revoked, or a value outside bounds that have since
narrowed, stops applying at once, before any stored override is cleaned up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Any, Final, Literal

from contexts.orchestration.domain.models import (
    AUTOSTOP_HARD_CAP,
    N_MAX,
    N_MIN,
    T_MINUTES_MAX,
    T_MINUTES_MIN,
    CountMode,
    EveryNMessagesTrigger,
    OnJoinTrigger,
    SilenceMinutesTrigger,
    WakeupConfig,
    WakeupSoftBounds,
    clamp,
)
from shared_kernel.type_guards import is_plain_int

Source = Literal["designer", "room", "runtime"]

#: Every field a room override may set ([R15.25]). ``autostop_rounds`` is the
#: binding role's own cap: ``autostop_rounds`` for a normal binding,
#: ``observer_autostop_rounds`` for an observer.
ROOM_FIELDS: Final = (
    "every_n_messages_enabled",
    "every_n_messages",
    "count",
    "silence_minutes_enabled",
    "silence_minutes",
    "autostop_rounds",
    "on_join_enabled",
)
#: The subset the agent's own tool may set ([R15.06]).
RUNTIME_FIELDS: Final = (
    "every_n_messages_enabled",
    "every_n_messages",
    "silence_minutes_enabled",
    "silence_minutes",
)


@dataclass(frozen=True, slots=True)
class WakeupOverrides:
    """A sparse layer: ``None`` means "inherit from the layer below"."""

    every_n_messages_enabled: bool | None = None
    every_n_messages: int | None = None
    count: CountMode | None = None
    silence_minutes_enabled: bool | None = None
    silence_minutes: int | None = None
    autostop_rounds: int | None = None
    on_join_enabled: bool | None = None

    @classmethod
    def from_dict(cls, raw: Any, *, allowed: tuple[str, ...] = ROOM_FIELDS) -> WakeupOverrides:
        """Tolerant parse of a stored layer: a wrong-typed field is absent, never a value."""
        data = raw if isinstance(raw, Mapping) else {}
        values: dict[str, Any] = {}
        for name in allowed:
            value = data.get(name)
            if name.endswith("_enabled"):
                if isinstance(value, bool):
                    values[name] = value
            elif name == "count":
                if value in ("all", "human"):
                    values[name] = value
            elif is_plain_int(value):
                values[name] = value
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None}

    def is_empty(self) -> bool:
        return not self.to_dict()

    def set_fields(self) -> frozenset[str]:
        return frozenset(self.to_dict())

    def merged(self, patch: WakeupOverrides) -> WakeupOverrides:
        """This layer with every field ``patch`` sets replaced."""
        return replace(self, **patch.to_dict())

    def without(self, names: frozenset[str]) -> WakeupOverrides:
        return replace(self, **dict.fromkeys(names & self.set_fields()))


@dataclass(frozen=True, slots=True)
class EffectiveWakeup:
    config: WakeupConfig
    #: Where each overridable field's effective value came from.
    sources: Mapping[str, Source]
    #: Runtime fields stored but not applied under the current defaults (a
    #: revoked toggle, designer ``call_only``); for reconciliation and display.
    ignored_runtime: frozenset[str]


def clamp_n(value: int, soft: WakeupSoftBounds | None) -> int:
    bounds = soft or WakeupSoftBounds()
    return clamp(value, max(N_MIN, bounds.n_min or N_MIN), min(N_MAX, bounds.n_max or N_MAX))


def clamp_t(value: int, soft: WakeupSoftBounds | None) -> int:
    bounds = soft or WakeupSoftBounds()
    return clamp(
        value,
        max(T_MINUTES_MIN, bounds.t_minutes_min or T_MINUTES_MIN),
        min(T_MINUTES_MAX, bounds.t_minutes_max or T_MINUTES_MAX),
    )


def _autostop_value(value: int) -> int:
    return clamp(value, 1, AUTOSTOP_HARD_CAP)


def resolve_room_wakeup(
    defaults: WakeupConfig,
    room: WakeupOverrides,
    runtime: WakeupOverrides,
    *,
    is_observer: bool,
) -> EffectiveWakeup:
    """The effective config for one live binding.

    Human room values are bounded by the hard limits only, as a designer's are:
    soft bounds (R15.08) limit what the *agent* may choose. Runtime values are
    clamped to the soft bounds, and a runtime ``enabled`` flag applies only while
    the designer still lists that trigger in ``self_toggle`` and has not set
    ``call_only``. Runtime flags that would leave both autonomous triggers off are
    dropped together: the agent may not silence itself completely ([R15.06a]),
    although the room's human may pause both.
    """
    every = defaults.triggers.every_n_messages
    silence = defaults.triggers.silence_minutes
    sources: dict[str, Source] = dict.fromkeys(ROOM_FIELDS, "designer")

    def take(name: str, value: Any, source: Source) -> Any:
        sources[name] = source
        return value

    every_enabled, every_n, count = every.enabled, every.n, every.count
    silence_enabled, t_minutes = silence.enabled, silence.t_minutes
    autostop = silence.autostop_limit_for(is_observer=is_observer)

    if room.every_n_messages_enabled is not None:
        every_enabled = take("every_n_messages_enabled", room.every_n_messages_enabled, "room")
    if room.every_n_messages is not None:
        every_n = take("every_n_messages", clamp(room.every_n_messages, N_MIN, N_MAX), "room")
    if room.count is not None:
        count = take("count", room.count, "room")
    if room.silence_minutes_enabled is not None:
        silence_enabled = take("silence_minutes_enabled", room.silence_minutes_enabled, "room")
    if room.silence_minutes is not None:
        t_minutes = take("silence_minutes", clamp(room.silence_minutes, T_MINUTES_MIN, T_MINUTES_MAX), "room")
    if room.autostop_rounds is not None:
        autostop = take("autostop_rounds", _autostop_value(room.autostop_rounds), "room")
    # [R15.26]: room layer only, never runtime, and never for an observer binding.
    on_join_enabled = defaults.triggers.on_join.enabled
    if room.on_join_enabled is not None:
        on_join_enabled = take("on_join_enabled", room.on_join_enabled, "room")
    if is_observer:
        on_join_enabled = False

    ignored: set[str] = set()
    if runtime.every_n_messages is not None:
        every_n = take("every_n_messages", clamp_n(runtime.every_n_messages, defaults.soft_bounds), "runtime")
    if runtime.silence_minutes is not None:
        t_minutes = take("silence_minutes", clamp_t(runtime.silence_minutes, defaults.soft_bounds), "runtime")
    flags: dict[str, bool] = {}
    for name, trigger, value in (
        ("every_n_messages_enabled", "every_n_messages", runtime.every_n_messages_enabled),
        ("silence_minutes_enabled", "silence_minutes", runtime.silence_minutes_enabled),
    ):
        if value is None:
            continue
        if defaults.triggers.call_only.enabled or trigger not in defaults.self_toggle:
            ignored.add(name)
        else:
            flags[name] = value
    if flags:
        every_after = flags.get("every_n_messages_enabled", every_enabled)
        silence_after = flags.get("silence_minutes_enabled", silence_enabled)
        if not every_after and not silence_after and (every_enabled or silence_enabled):
            ignored.update(flags)
        else:
            if "every_n_messages_enabled" in flags:
                every_enabled = take("every_n_messages_enabled", flags["every_n_messages_enabled"], "runtime")
            if "silence_minutes_enabled" in flags:
                silence_enabled = take("silence_minutes_enabled", flags["silence_minutes_enabled"], "runtime")

    role_cap = "observer_autostop_rounds" if is_observer else "autostop_rounds"
    silence_trigger: SilenceMinutesTrigger = replace(
        silence, enabled=silence_enabled, t_minutes=t_minutes, **{role_cap: autostop}
    )
    config = replace(
        defaults,
        triggers=replace(
            defaults.triggers,
            every_n_messages=EveryNMessagesTrigger(enabled=every_enabled, n=every_n, count=count),
            silence_minutes=silence_trigger,
            on_join=OnJoinTrigger(enabled=on_join_enabled),
        ),
    )
    return EffectiveWakeup(config=config, sources=sources, ignored_runtime=frozenset(ignored))


def apply_room_patch(
    room: WakeupOverrides, runtime: WakeupOverrides, patch: WakeupOverrides
) -> tuple[WakeupOverrides, WakeupOverrides]:
    """A human room change: the supplied fields replace the room layer's, and the
    runtime layer loses the same fields, so saving one setting never adopts the
    agent's value for another ([R15.25])."""
    changed = patch.set_fields()
    return room.merged(patch), runtime.without(changed)


__all__ = [
    "ROOM_FIELDS",
    "RUNTIME_FIELDS",
    "EffectiveWakeup",
    "Source",
    "WakeupOverrides",
    "apply_room_patch",
    "clamp_n",
    "clamp_t",
    "resolve_room_wakeup",
]
