"""Room-owned wake-up settings: reads, the agent's tool, human edits and refresh.

The three layers and their resolution are ``domain.room_wakeup``; this service
loads them for one live binding, writes them with optimistic concurrency, and
reports the timer side effects a change implies ([R15.09]):

- no configuration change resets autostop;
- switching the message trigger on, or changing its ``n`` or ``count``, restarts
  its message count and discards its deferred firing;
- switching the message trigger off discards its deferred firing;
- switching silence on, or changing its interval, re-arms that binding's
  silence clock now, so the change cannot fire a reply at once;
- an effective no-op writes nothing and touches no timer.

The timers live in Redis and the write in the caller's transaction, so a write
returns its :class:`TimerEffects` instead of applying them: the caller applies
them with :func:`apply_timer_effects` after that transaction commits, and a
rollback leaves the timers untouched.

The runtime layer's refresh interval starts at its first actual override and is
not postponed by later tuning; refresh clears that layer only. Every write
names the binding incarnation it read, so a write against an unbound or rebound
binding fails instead of landing on its successor.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.interfaces.facade import Agent, AgentsFacade
from contexts.conversation.interfaces.facade import BindingWakeupState, ChatroomAgentRole, ConversationFacade
from contexts.orchestration.domain.errors import (
    RoomWakeupBindingGone,
    RoomWakeupVersionConflict,
    WakeupToggleNotAllowed,
)
from contexts.orchestration.domain.models import WakeupConfig
from contexts.orchestration.domain.room_wakeup import (
    RUNTIME_FIELDS,
    EffectiveWakeup,
    WakeupOverrides,
    apply_room_patch,
    clamp_n,
    clamp_t,
    resolve_room_wakeup,
)
from contexts.orchestration.infrastructure import wakeup_state
from shared_kernel import audit
from shared_kernel.auth.clients import now

ResetScope = Literal["runtime", "room"]


@dataclass(frozen=True, slots=True)
class RoomWakeup:
    """One live binding's settings, as every consumer sees them."""

    state: BindingWakeupState
    agent: Agent
    defaults: WakeupConfig
    room: WakeupOverrides
    runtime: WakeupOverrides
    effective: EffectiveWakeup

    @property
    def is_observer(self) -> bool:
        return self.state.role is ChatroomAgentRole.OBSERVER

    @property
    def refresh_due_at(self) -> datetime | None:
        started = self.state.runtime_started_at
        if started is None:
            return None
        try:
            return started + timedelta(hours=self.defaults.refresh_every_hours)
        except OverflowError:
            # An interval past datetime's range never comes due.
            return None


@dataclass(frozen=True, slots=True)
class TimerEffects:
    """The timer changes one committed write implies, for one binding."""

    agent_id: uuid.UUID
    room_id: uuid.UUID
    binding_id: uuid.UUID
    drop_deferred_fire: bool = False
    restart_message_count: bool = False
    rearm_silence: bool = False

    def is_empty(self) -> bool:
        return not (self.drop_deferred_fire or self.restart_message_count or self.rearm_silence)


@dataclass(frozen=True, slots=True)
class RoomWakeupChange:
    """A human write's result: the settings now stored, and the timers still owed."""

    wakeup: RoomWakeup
    effects: TimerEffects | None


@dataclass(frozen=True, slots=True)
class RuntimeTuning:
    """The agent tool's result: its effective config, and the timers still owed."""

    config: WakeupConfig
    effects: TimerEffects | None


def timer_effects(before: RoomWakeup, after: RoomWakeup) -> TimerEffects | None:
    old, new = before.effective.config.triggers, after.effective.config.triggers
    old_n, new_n = old.every_n_messages, new.every_n_messages
    state = after.state
    effects = TimerEffects(
        agent_id=state.agent_id,
        room_id=state.chatroom_id,
        binding_id=state.binding_id,
        drop_deferred_fire=old_n.enabled and not new_n.enabled,
        # The trigger fires on `count % n == 0` over a running count, so a new n
        # left on the old count would fire after anywhere from 1 to n messages.
        restart_message_count=new_n.enabled
        and (not old_n.enabled or old_n.n != new_n.n or old_n.count != new_n.count),
        rearm_silence=new.silence_minutes.enabled
        and (
            not old.silence_minutes.enabled or old.silence_minutes.t_minutes != new.silence_minutes.t_minutes
        ),
    )
    return None if effects.is_empty() else effects


async def apply_timer_effects(effects: Iterable[TimerEffects | None]) -> None:
    """Apply effects whose writes have committed. Raises on a Redis failure, which
    the caller logs: a missed effect can let one reply fire early, never lose one."""
    for effect in effects:
        if effect is None:
            continue
        if effect.restart_message_count:
            await wakeup_state.restart_message_count(effect.agent_id, effect.room_id, effect.binding_id)
        elif effect.drop_deferred_fire:
            await wakeup_state.pop_fire_due(effect.agent_id, effect.room_id, effect.binding_id)
        if effect.rearm_silence:
            await wakeup_state.touch_silence_timestamp(effect.agent_id, effect.room_id, effect.binding_id)


def build_room_wakeup(state: BindingWakeupState, agent: Agent) -> RoomWakeup:
    defaults = WakeupConfig.from_dict(agent.wakeup_config)
    room = WakeupOverrides.from_dict(state.room_overrides)
    runtime = WakeupOverrides.from_dict(state.runtime_overrides, allowed=RUNTIME_FIELDS)
    effective = resolve_room_wakeup(
        defaults, room, runtime, is_observer=state.role is ChatroomAgentRole.OBSERVER
    )
    return RoomWakeup(
        state=state, agent=agent, defaults=defaults, room=room, runtime=runtime, effective=effective
    )


class RoomWakeupService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._agents = AgentsFacade(db)
        self._conversation = ConversationFacade(db)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def load(self, *, agent_id: uuid.UUID, room_id: uuid.UUID) -> RoomWakeup | None:
        """The live binding's settings, or ``None`` when unbound or the agent is gone."""
        state = await self._conversation.binding_wakeup_state(chatroom_id=room_id, agent_id=agent_id)
        if state is None:
            return None
        return await self.for_state(state)

    async def for_state(self, state: BindingWakeupState, agent: Agent | None = None) -> RoomWakeup | None:
        agent = agent if agent is not None else await self._agents.get_agent(state.agent_id)
        if agent is None or agent.deleted_at is not None:
            return None
        return build_room_wakeup(state, agent)

    # ------------------------------------------------------------------
    # The agent's own tool ([R15.06], [R15.06a])
    # ------------------------------------------------------------------

    async def update_runtime(
        self,
        *,
        agent_id: uuid.UUID,
        room_id: uuid.UUID,
        binding_id: uuid.UUID,
        every_n_messages: int | None = None,
        silence_minutes: int | None = None,
        every_n_messages_enabled: bool | None = None,
        silence_minutes_enabled: bool | None = None,
    ) -> RuntimeTuning:
        """Change this binding's runtime layer; never another room or the defaults.

        A bounded retry starts again from the original request against the latest
        policy. The binding identity comes from the runtime, never from the model.
        """
        toggles = {
            name: value
            for name, value in (
                ("every_n_messages", every_n_messages_enabled),
                ("silence_minutes", silence_minutes_enabled),
            )
            if value is not None
        }
        for attempt in range(2):
            current = await self._live(agent_id=agent_id, room_id=room_id, binding_id=binding_id)
            self._check_toggles(current.effective.config, current.defaults, toggles)
            clamped: dict[str, dict[str, int]] = {}
            patch: dict[str, Any] = {}
            if every_n_messages is not None:
                patch["every_n_messages"] = clamp_n(every_n_messages, current.defaults.soft_bounds)
                if patch["every_n_messages"] != every_n_messages:
                    clamped["every_n_messages"] = {
                        "requested": every_n_messages,
                        "clamped_to": patch["every_n_messages"],
                    }
            if silence_minutes is not None:
                patch["silence_minutes"] = clamp_t(silence_minutes, current.defaults.soft_bounds)
                if patch["silence_minutes"] != silence_minutes:
                    clamped["silence_minutes"] = {
                        "requested": silence_minutes,
                        "clamped_to": patch["silence_minutes"],
                    }
            for name, value in toggles.items():
                patch[f"{name}_enabled"] = value
            runtime = current.runtime.merged(WakeupOverrides(**patch))
            resolved = resolve_room_wakeup(
                current.defaults, current.room, runtime, is_observer=current.is_observer
            )
            if resolved.config == current.effective.config:
                # An effective no-op writes nothing: no version, no timer ([R15.06a]).
                runtime = current.runtime
            updated = await self._write(current, room=current.room, runtime=runtime)
            if updated is None and attempt == 0:
                continue
            if updated is None:
                raise RoomWakeupVersionConflict(
                    "this room's wake-up settings kept changing while you wrote; try again"
                )
            await self._audit_tool(current, clamped=clamped, toggles=toggles, changed=updated is not current)
            return RuntimeTuning(config=updated.effective.config, effects=timer_effects(current, updated))
        raise RoomWakeupVersionConflict(str(binding_id))  # pragma: no cover - loop always returns

    @staticmethod
    def _check_toggles(effective: WakeupConfig, defaults: WakeupConfig, toggles: dict[str, bool]) -> None:
        """Refuse a toggle the designer did not allow, or one that silences the agent."""
        if not toggles:
            return
        if defaults.triggers.call_only.enabled:
            raise WakeupToggleNotAllowed(
                "this agent only answers when called; its triggers cannot be switched"
            )
        refused = sorted(name for name in toggles if name not in defaults.self_toggle)
        if refused:
            raise WakeupToggleNotAllowed(
                f"not allowed to switch {', '.join(refused)}: "
                "the designer has not enabled self-toggling for it"
            )
        every_on = toggles.get("every_n_messages", effective.triggers.every_n_messages.enabled)
        silence_on = toggles.get("silence_minutes", effective.triggers.silence_minutes.enabled)
        if not every_on and not silence_on:
            raise WakeupToggleNotAllowed(
                "refused: this would turn off every trigger, and the agent could not wake on its own again"
            )

    async def _audit_tool(
        self,
        current: RoomWakeup,
        *,
        clamped: dict[str, dict[str, int]],
        toggles: dict[str, bool],
        changed: bool,
    ) -> None:
        scope = {"room_id": str(current.state.chatroom_id), "binding_id": str(current.state.binding_id)}
        # `isolated`: reached from the tool on the turn's own session, whose reply
        # commit this write must not abort.
        if clamped:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="agent.wakeup_clamped",
                    resource_type="agent",
                    resource_id=current.agent.id,
                    metadata={**scope, "clamped_fields": clamped, "actor_agent_id": str(current.agent.id)},
                ),
                isolated=True,
            )
        for name, value in toggles.items():
            # Every accepted toggle, changed or not ([R15.06a]).
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="agent.wakeup_toggled",
                    resource_type="agent",
                    resource_id=current.agent.id,
                    metadata={
                        **scope,
                        "trigger": name,
                        "enabled": value,
                        "changed": changed,
                        "actor_agent_id": str(current.agent.id),
                    },
                ),
                isolated=True,
            )

    # ------------------------------------------------------------------
    # Human room settings ([R15.25])
    # ------------------------------------------------------------------

    async def patch_room(
        self,
        *,
        agent_id: uuid.UUID,
        room_id: uuid.UUID,
        binding_id: uuid.UUID,
        expected_version: int,
        patch: WakeupOverrides,
        actor_user_id: uuid.UUID,
    ) -> RoomWakeupChange:
        current = await self._live(agent_id=agent_id, room_id=room_id, binding_id=binding_id)
        if current.state.version != expected_version:
            raise RoomWakeupVersionConflict(str(binding_id))
        room, runtime = apply_room_patch(current.room, current.runtime, patch)
        updated = await self._write(current, room=room, runtime=runtime)
        if updated is None:
            raise RoomWakeupVersionConflict(str(binding_id))
        if updated is not current:
            await self._audit_human(
                current, "chatroom.agent_wakeup_updated", actor_user_id, sorted(patch.set_fields())
            )
        return RoomWakeupChange(wakeup=updated, effects=timer_effects(current, updated))

    async def reset(
        self,
        *,
        agent_id: uuid.UUID,
        room_id: uuid.UUID,
        binding_id: uuid.UUID,
        expected_version: int,
        scope: ResetScope,
        actor_user_id: uuid.UUID,
    ) -> RoomWakeupChange:
        """``runtime`` drops the agent's adjustments; ``room`` returns every field to the defaults."""
        current = await self._live(agent_id=agent_id, room_id=room_id, binding_id=binding_id)
        if current.state.version != expected_version:
            raise RoomWakeupVersionConflict(str(binding_id))
        room = WakeupOverrides() if scope == "room" else current.room
        updated = await self._write(current, room=room, runtime=WakeupOverrides())
        if updated is None:
            raise RoomWakeupVersionConflict(str(binding_id))
        if updated is not current:
            await self._audit_human(current, "chatroom.agent_wakeup_reset", actor_user_id, [scope])
        return RoomWakeupChange(wakeup=updated, effects=timer_effects(current, updated))

    async def _audit_human(
        self, current: RoomWakeup, action: str, actor_user_id: uuid.UUID, fields: list[str]
    ) -> None:
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                resource_type="chatroom",
                resource_id=current.state.chatroom_id,
                metadata={
                    "agent_id": str(current.agent.id),
                    "binding_id": str(current.state.binding_id),
                    "fields": fields,
                },
            ),
        )

    # ------------------------------------------------------------------
    # Refresh ([R15.09])
    # ------------------------------------------------------------------

    async def refresh_if_due(
        self, state: BindingWakeupState, *, at: datetime | None = None
    ) -> RoomWakeupChange | None:
        """Clear the binding's runtime layer once its interval has elapsed; ``None`` when not.

        The interval is the designer's current one measured from the existing
        start, so shortening it can make a binding due at once.
        """
        current = await self.for_state(state)
        if current is None or current.refresh_due_at is None:
            return None
        if (at or now()) < current.refresh_due_at:
            return None
        updated = await self._write(current, room=current.room, runtime=WakeupOverrides())
        if updated is None or updated is current:
            return None
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.wakeup_refreshed",
                resource_type="agent",
                resource_id=current.agent.id,
                metadata={
                    "room_id": str(state.chatroom_id),
                    "binding_id": str(state.binding_id),
                    "previous": current.runtime.to_dict(),
                },
            ),
        )
        return RoomWakeupChange(wakeup=updated, effects=timer_effects(current, updated))

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    async def _live(self, *, agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> RoomWakeup:
        current = await self.load(agent_id=agent_id, room_id=room_id)
        if current is None or current.state.binding_id != binding_id:
            raise RoomWakeupBindingGone(
                "this agent is no longer bound to the room these settings belong to; reload them"
            )
        return current

    async def _write(
        self, current: RoomWakeup, *, room: WakeupOverrides, runtime: WakeupOverrides
    ) -> RoomWakeup | None:
        """CAS both layers; ``current`` itself when nothing changes, ``None`` on conflict."""
        if room == current.room and runtime == current.runtime:
            return current
        started = current.state.runtime_started_at
        if runtime.is_empty():
            started = None
        elif current.runtime.is_empty():
            started = now()
        state = await self._conversation.write_binding_wakeup_layers(
            binding_id=current.state.binding_id,
            expected_version=current.state.version,
            room_overrides=room.to_dict(),
            runtime_overrides=runtime.to_dict(),
            runtime_started_at=started,
        )
        if state is None:
            return None
        return build_room_wakeup(state, current.agent)


__all__ = [
    "ResetScope",
    "RoomWakeup",
    "RoomWakeupChange",
    "RoomWakeupService",
    "RuntimeTuning",
    "TimerEffects",
    "apply_timer_effects",
    "build_room_wakeup",
    "timer_effects",
]
