"""Three-layer wake-up resolution for one room binding ([R15.09], [R15.06a], [R15.25]).

Spec: ``docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence``
§6.3 (AC-7, AC-8). Every consumer resolves through this one pure function, so the
designer's current policy applies the moment it changes, whatever is stored.
"""

from __future__ import annotations

from typing import Any

from contexts.orchestration.domain.models import N_MAX, WakeupConfig
from contexts.orchestration.domain.room_wakeup import (
    ROOM_FIELDS,
    RUNTIME_FIELDS,
    WakeupOverrides,
    apply_room_patch,
    resolve_room_wakeup,
)


def _defaults(**extra: Any) -> WakeupConfig:
    raw: dict[str, Any] = {
        "triggers": {
            "every_n_messages": {"enabled": True, "n": 3, "count": "all"},
            "silence_minutes": {
                "enabled": True,
                "t_minutes": 2,
                "autostop_rounds": 4,
                "observer_autostop_rounds": 40,
            },
        },
        "self_toggle": ["every_n_messages", "silence_minutes"],
        "soft_bounds": {"n_min": 2, "n_max": 10, "t_minutes_min": 1, "t_minutes_max": 30},
    }
    raw.update(extra)
    return WakeupConfig.from_dict(raw)


def _resolve(
    defaults: WakeupConfig | None = None,
    *,
    room: WakeupOverrides | None = None,
    runtime: WakeupOverrides | None = None,
    observer: bool = False,
):
    return resolve_room_wakeup(
        defaults or _defaults(), room or WakeupOverrides(), runtime or WakeupOverrides(), is_observer=observer
    )


class TestLayering:
    def test_no_overrides_is_the_designer_config(self) -> None:
        eff = _resolve()

        assert eff.config == _defaults()
        assert set(eff.sources.values()) == {"designer"}

    def test_room_then_runtime(self) -> None:
        eff = _resolve(
            room=WakeupOverrides(every_n_messages=5, silence_minutes=9, count="human"),
            runtime=WakeupOverrides(every_n_messages=7),
        )

        assert eff.config.triggers.every_n_messages.n == 7
        assert eff.config.triggers.every_n_messages.count == "human"
        assert eff.config.triggers.silence_minutes.t_minutes == 9
        assert eff.sources["every_n_messages"] == "runtime"
        assert eff.sources["silence_minutes"] == "room"
        assert eff.sources["count"] == "room"

    def test_designer_only_policy_cannot_be_overridden(self) -> None:
        defaults = _defaults(allow_self_open=False, refresh_every_hours=6)
        eff = _resolve(defaults, room=WakeupOverrides(every_n_messages=5))

        assert eff.config.self_toggle == defaults.self_toggle
        assert eff.config.soft_bounds == defaults.soft_bounds
        assert eff.config.refresh_every_hours == 6
        assert eff.config.allow_self_open is False


class TestBounds:
    def test_a_human_room_value_uses_the_hard_bounds_only(self) -> None:
        eff = _resolve(room=WakeupOverrides(every_n_messages=50, silence_minutes=5000))

        # Soft bounds limit the agent, not the teacher.
        assert eff.config.triggers.every_n_messages.n == 50
        assert eff.config.triggers.silence_minutes.t_minutes == 1440

    def test_a_runtime_value_is_clamped_to_the_current_soft_bounds(self) -> None:
        eff = _resolve(runtime=WakeupOverrides(every_n_messages=50, silence_minutes=0))

        assert eff.config.triggers.every_n_messages.n == 10
        assert eff.config.triggers.silence_minutes.t_minutes == 1

    def test_narrowed_bounds_apply_to_a_stored_runtime_value(self) -> None:
        narrowed = _defaults(soft_bounds={"n_min": 2, "n_max": 4})
        eff = _resolve(narrowed, runtime=WakeupOverrides(every_n_messages=9))

        assert eff.config.triggers.every_n_messages.n == 4

    def test_the_room_value_is_hard_capped(self) -> None:
        eff = _resolve(room=WakeupOverrides(every_n_messages=N_MAX + 5, autostop_rounds=500))

        assert eff.config.triggers.every_n_messages.n == N_MAX
        assert eff.config.triggers.silence_minutes.autostop_rounds == 100


class TestToggles:
    def test_a_revoked_toggle_stops_applying(self) -> None:
        revoked = _defaults(self_toggle=["every_n_messages"])
        eff = _resolve(revoked, runtime=WakeupOverrides(silence_minutes_enabled=False))

        assert eff.config.triggers.silence_minutes.enabled is True
        assert eff.ignored_runtime == {"silence_minutes_enabled"}

    def test_designer_call_only_ignores_every_runtime_flag(self) -> None:
        call_only = _defaults(
            triggers={"every_n_messages": {"enabled": True}, "call_only": {"enabled": True}}
        )
        eff = _resolve(call_only, runtime=WakeupOverrides(every_n_messages_enabled=False))

        assert eff.config.triggers.call_only.enabled is True
        assert eff.ignored_runtime == {"every_n_messages_enabled"}

    def test_the_agent_cannot_silence_itself_completely(self) -> None:
        eff = _resolve(runtime=WakeupOverrides(every_n_messages_enabled=False, silence_minutes_enabled=False))

        assert eff.config.triggers.every_n_messages.enabled is True
        assert eff.config.triggers.silence_minutes.enabled is True
        assert eff.ignored_runtime == {"every_n_messages_enabled", "silence_minutes_enabled"}

    def test_the_agent_may_switch_one_off_when_the_other_stays_on(self) -> None:
        eff = _resolve(runtime=WakeupOverrides(silence_minutes_enabled=False))

        assert eff.config.triggers.silence_minutes.enabled is False
        assert eff.sources["silence_minutes_enabled"] == "runtime"

    def test_a_human_may_pause_both(self) -> None:
        eff = _resolve(room=WakeupOverrides(every_n_messages_enabled=False, silence_minutes_enabled=False))

        assert eff.config.triggers.every_n_messages.enabled is False
        assert eff.config.triggers.silence_minutes.enabled is False

    def test_a_runtime_flag_cannot_reopen_what_the_human_paused(self) -> None:
        """Turning one on is allowed; the all-off rule is about switching off."""
        eff = _resolve(
            room=WakeupOverrides(every_n_messages_enabled=False, silence_minutes_enabled=False),
            runtime=WakeupOverrides(silence_minutes_enabled=False),
        )

        assert eff.config.triggers.silence_minutes.enabled is False


class TestRoles:
    def test_an_observer_override_sets_the_observer_cap(self) -> None:
        eff = _resolve(room=WakeupOverrides(autostop_rounds=7), observer=True)

        assert eff.config.triggers.silence_minutes.autostop_limit_for(is_observer=True) == 7
        assert eff.config.triggers.silence_minutes.autostop_rounds == 4

    def test_a_normal_override_sets_the_normal_cap(self) -> None:
        eff = _resolve(room=WakeupOverrides(autostop_rounds=7))

        assert eff.config.triggers.silence_minutes.autostop_limit_for(is_observer=False) == 7
        assert eff.config.triggers.silence_minutes.observer_autostop_rounds == 40


class TestPatches:
    def test_a_room_patch_clears_the_same_runtime_fields_only(self) -> None:
        room, runtime = apply_room_patch(
            WakeupOverrides(silence_minutes=9),
            WakeupOverrides(every_n_messages=7, silence_minutes=3),
            WakeupOverrides(every_n_messages=5),
        )

        assert room == WakeupOverrides(every_n_messages=5, silence_minutes=9)
        assert runtime == WakeupOverrides(silence_minutes=3)

    def test_stored_layers_parse_tolerantly(self) -> None:
        raw = {
            "every_n_messages": True,
            "silence_minutes_enabled": "false",
            "count": "bogus",
            "x": 1,
            "silence_minutes": 4,
        }

        assert WakeupOverrides.from_dict(raw) == WakeupOverrides(silence_minutes=4)
        assert WakeupOverrides.from_dict("not a dict") == WakeupOverrides()

    def test_a_runtime_layer_keeps_only_runtime_fields(self) -> None:
        raw = {"count": "human", "autostop_rounds": 3, "every_n_messages": 4}

        assert WakeupOverrides.from_dict(raw, allowed=RUNTIME_FIELDS) == WakeupOverrides(every_n_messages=4)
        assert set(RUNTIME_FIELDS) < set(ROOM_FIELDS)

    def test_round_trip(self) -> None:
        layer = WakeupOverrides(every_n_messages=4, count="human", silence_minutes_enabled=False)

        assert WakeupOverrides.from_dict(layer.to_dict()) == layer
