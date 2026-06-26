"""Wake-up evaluator & trigger service (G.3 + G.4 + G.5).

Responsibilities:
- Evaluate whether a wake-up trigger should fire for a given agent+room.
- Handle ``message.created`` events from the conversation context.
- Handle presence changes to pause/resume silence timers (R15.05b).
- Self-modification of wake-up params (G.4) with clamping.
- Periodic refresh to snap config back to authored values (G.5).

SoC:
- WakeupConfig parsing → ``domain.models.WakeupConfig``
- Redis counters/timers → ``infrastructure.wakeup_state``
- Agent reads + writes → ``AgentsFacade``
- Audit → ``shared_kernel.audit``
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.interfaces.facade import (
    Agent,
    AgentDraft,
    AgentsFacade,
    AgentVersionMismatch,
)
from contexts.conversation.infrastructure.presence import PresenceTracker
from contexts.orchestration.domain.models import (
    N_MAX,
    N_MIN,
    T_MINUTES_MAX,
    T_MINUTES_MIN,
    WakeupConfig,
    WakeupSoftBounds,
)
from contexts.orchestration.infrastructure import wakeup_state
from contexts.orchestration.infrastructure.metrics import WAKEUP_FIRES
from shared_kernel import audit

logger = logging.getLogger(__name__)


class WakeupService:
    """Application-level wake-up trigger evaluator (G.3)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._agents_facade = AgentsFacade(db)
        self._presence = PresenceTracker()

    # ------------------------------------------------------------------
    # Event: message.created in a room
    # ------------------------------------------------------------------

    async def on_message_created(
        self,
        *,
        room_id: uuid.UUID,
        sender_is_user: bool,
        agent_ids: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        """Called when any message is created in a room.

        ``agent_ids`` is the set of agents bound to this room.
        Returns the list of agent_ids that should wake up.

        Evaluates every_n_messages trigger (R15.01) and resets
        autostop when a user sends a message.
        """
        wake_list: list[uuid.UUID] = []

        for agent_id in agent_ids:
            agent = await self._agents_facade.get_agent(agent_id)
            if agent is None or agent.deleted_at is not None:
                continue

            cfg = WakeupConfig.from_dict(agent.wakeup_config)
            if cfg.is_inert() or cfg.triggers.call_only.enabled:
                continue

            # Touch silence timer on any activity.
            await wakeup_state.touch_silence_timestamp(agent_id, room_id)

            if sender_is_user:
                await wakeup_state.reset_autostop(agent_id, room_id)

            # every_n_messages: counts user-sent messages only (agent replies
            # don't flow through evaluate_message_wakeups to avoid self-trigger loops).
            if cfg.triggers.every_n_messages.enabled:
                count = await wakeup_state.increment_message_count(agent_id, room_id)
                n = cfg.triggers.every_n_messages.n
                if n > 0 and count % n == 0:
                    # Check allow_self_open: if room is empty and flag is false, skip.
                    if not cfg.allow_self_open:
                        members = await self._presence.list_room(room_id)
                        if not members:
                            # The trigger fired but the agent may not open an
                            # empty room on its own — otherwise this silence is a
                            # mystery to the owner. Heads-up, debounced per room.
                            await self._notify_wakeup_gated(agent, room_id)
                            continue
                    WAKEUP_FIRES.labels(kind="every_n_messages").inc()
                    wake_list.append(agent_id)

        return wake_list

    async def _notify_wakeup_gated(self, agent: Agent, room_id: uuid.UUID) -> None:
        """Tell the project's owners that ``agent``'s ``every_n_messages`` wake-up
        was suppressed because nobody was present and ``allow_self_open`` is off.

        Debounced to at most once an hour per (agent, room) so an active room
        cannot spam the bell. The caller's message is already durably committed
        (the wake-up dispatch is post-commit), so we persist + publish our own
        notification rows here rather than leaving them to a trailing commit a
        best-effort rollback might discard.
        """
        if not await wakeup_state.claim_gated_notice(agent.id, room_id):
            return
        # Function-local imports keep the orchestration→notification/tenancy edges
        # out of module import (mirrors the deferred-import convention in
        # conversation.application.triggers).
        from contexts.notification.interfaces.facade import (
            NotificationFacade,
            NotificationKind,
        )
        from contexts.tenancy.domain.models import ProjectMemberRole
        from contexts.tenancy.interfaces.facade import TenancyFacade

        members = await TenancyFacade(self._db).project_members(agent.project_id)
        owners = [m.user_id for m in members if m.role is ProjectMemberRole.OWNER]
        if not owners:
            return

        notif = NotificationFacade(self._db)
        body = (
            f"{agent.name} reached its message trigger but did not open the room: "
            "no one was present and it is not allowed to open the room on its own. "
            "Enable self-open in the agent's wake-up settings to let it reply while you are away."
        )
        for uid in owners:
            await notif.send(
                user_id=uid,
                kind=NotificationKind.AGENT_WAKEUP_GATED,
                title="An agent stayed quiet while you were away",
                body=body,
                metadata={
                    "agent_id": str(agent.id),
                    "room_id": str(room_id),
                    "reason": "presence_gated",
                    "trigger": "every_n_messages",
                },
            )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Silence timer evaluation (called periodically per agent+room)
    # ------------------------------------------------------------------

    async def evaluate_silence_trigger(
        self,
        *,
        agent_id: uuid.UUID,
        room_id: uuid.UUID,
    ) -> bool:
        """Check if silence_minutes trigger should fire (R15.02).

        Returns True if the agent should wake up due to silence.
        """
        agent = await self._agents_facade.get_agent(agent_id)
        if agent is None or agent.deleted_at is not None:
            return False

        cfg = WakeupConfig.from_dict(agent.wakeup_config)
        if not cfg.triggers.silence_minutes.enabled:
            return False
        if cfg.triggers.call_only.enabled:
            return False

        # R15.05b: only fire when live users are present.
        if not await wakeup_state.is_silence_active(agent_id, room_id):
            return False

        last_ts = await wakeup_state.get_silence_timestamp(agent_id, room_id)
        if last_ts is None:
            return False

        elapsed_minutes = (datetime.now(UTC) - last_ts).total_seconds() / 60.0
        if elapsed_minutes < cfg.triggers.silence_minutes.t_minutes:
            return False

        # Check autostop (R15.03 / R15.04).
        autostop_count = await wakeup_state.get_autostop_count(agent_id, room_id)
        if autostop_count >= cfg.triggers.silence_minutes.autostop_rounds:
            return False

        # R15.05: allow_self_open check.
        if not cfg.allow_self_open:
            members = await self._presence.list_room(room_id)
            if not members:
                return False

        WAKEUP_FIRES.labels(kind="silence_minutes").inc()
        return True

    # ------------------------------------------------------------------
    # Presence change: pause/resume silence timer (R15.05b)
    # ------------------------------------------------------------------

    async def on_presence_changed(
        self,
        *,
        room_id: uuid.UUID,
        agent_ids: list[uuid.UUID],
        has_live_users: bool,
    ) -> None:
        """Called when room presence changes.

        Starts silence timer when users join, pauses when room empties.
        """
        for agent_id in agent_ids:
            await wakeup_state.set_silence_active(agent_id, room_id, has_live_users)
            if has_live_users:
                await wakeup_state.touch_silence_timestamp(agent_id, room_id)

    # ------------------------------------------------------------------
    # Agent spoke without user reply → bump autostop
    # ------------------------------------------------------------------

    async def on_agent_message_sent(
        self,
        *,
        agent_id: uuid.UUID,
        room_id: uuid.UUID,
    ) -> int:
        """Track consecutive agent-only rounds for autostop (R15.03).

        Returns the new autostop count.
        """
        return await wakeup_state.increment_autostop(agent_id, room_id)

    # ------------------------------------------------------------------
    # G.4: Self-modification of wake-up config
    # ------------------------------------------------------------------

    async def update_wakeup(
        self,
        *,
        agent_id: uuid.UUID,
        every_n_messages: int | None = None,
        silence_minutes: int | None = None,
        actor_agent_id: uuid.UUID | None = None,
    ) -> WakeupConfig:
        """Built-in tool for agents to modify their own wake-up params (R15.06).

        Only ``every_n_messages.n`` and ``silence_minutes.t_minutes`` are
        writable at runtime. Values are clamped to hard bounds (R15.07)
        and designer soft bounds (R15.08).
        """
        agent = await self._agents_facade.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"agent {agent_id} not found")

        soft_bounds = self._parse_soft_bounds(agent)
        clamped_fields: dict[str, dict[str, Any]] = {}

        # Keep originals so the retry closure re-clamps from the user's
        # actual request against fresh bounds, not from an already-clamped value.
        requested_n = every_n_messages
        requested_t = silence_minutes

        if every_n_messages is not None:
            clamped = self._clamp_n(every_n_messages, soft_bounds)
            if clamped != every_n_messages:
                clamped_fields["every_n_messages"] = {
                    "requested": every_n_messages,
                    "clamped_to": clamped,
                }

        if silence_minutes is not None:
            clamped = self._clamp_t(silence_minutes, soft_bounds)
            if clamped != silence_minutes:
                clamped_fields["silence_minutes"] = {
                    "requested": silence_minutes,
                    "clamped_to": clamped,
                }

        # Persist via agent service (bumps version). Retry once on version
        # conflicts: wakeup workers and the periodic G.5 refresh can race to
        # write wakeup_config on the same agent. On retry, recompute the config
        # from the fresh agent state so we apply the requested deltas on top
        # of whatever the concurrent writer committed, not on top of stale data.
        def _build_new_dict(base_agent: Agent) -> dict[str, Any]:
            fresh_cfg = WakeupConfig.from_dict(base_agent.wakeup_config)
            d = fresh_cfg.to_dict()
            fresh_bounds = self._parse_soft_bounds(base_agent)
            if requested_n is not None:
                d["triggers"]["every_n_messages"]["n"] = self._clamp_n(requested_n, fresh_bounds)
            if requested_t is not None:
                d["triggers"]["silence_minutes"]["t_minutes"] = self._clamp_t(requested_t, fresh_bounds)
            return d

        for _attempt in range(2):
            draft = AgentDraft(wakeup_config=_build_new_dict(agent))
            try:
                updated = await self._agents_facade.patch_agent(
                    agent_id=agent_id,
                    draft=draft,
                    expected_version=agent.version,
                    actor_user_id=uuid.UUID(int=0),  # system actor
                    actor_ip=None,
                )
                break
            except AgentVersionMismatch:
                if _attempt == 1:
                    raise
                agent = await self._agents_facade.get_agent(agent_id)
                if agent is None:
                    raise ValueError(f"agent {agent_id} not found") from None

        if clamped_fields:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="agent.wakeup_clamped",
                    resource_type="agent",
                    resource_id=agent_id,
                    metadata={
                        "clamped_fields": clamped_fields,
                        "actor_agent_id": str(actor_agent_id) if actor_agent_id else None,
                    },
                ),
            )

        return WakeupConfig.from_dict(updated.wakeup_config)

    # ------------------------------------------------------------------
    # G.5: Periodic refresh
    # ------------------------------------------------------------------

    async def refresh_wakeup_config(self, agent_id: uuid.UUID) -> bool:
        """Snap wakeup_config back to the designer's authored values (R15.09).

        Returns True if a reset was applied (values differed).
        """
        agent = await self._agents_facade.get_agent(agent_id)
        if agent is None or agent.deleted_at is not None:
            return False

        authored = agent.wakeup_authored_snapshot
        if not authored:
            return False

        current = agent.wakeup_config
        if current == authored:
            return False

        draft = AgentDraft(wakeup_config=authored)
        for _attempt in range(2):
            try:
                await self._agents_facade.patch_agent(
                    agent_id=agent_id,
                    draft=draft,
                    expected_version=agent.version,
                    actor_user_id=uuid.UUID(int=0),  # system actor
                    actor_ip=None,
                )
                break
            except AgentVersionMismatch:
                if _attempt == 1:
                    raise
                agent = await self._agents_facade.get_agent(agent_id)
                if agent is None or agent.deleted_at is not None:
                    return False
                authored = agent.wakeup_authored_snapshot
                if not authored or agent.wakeup_config == authored:
                    return False
                draft = AgentDraft(wakeup_config=authored)

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.wakeup_refreshed",
                resource_type="agent",
                resource_id=agent_id,
                metadata={
                    "previous": current,
                    "restored_to": authored,
                },
            ),
        )
        return True

    # ------------------------------------------------------------------
    # Clamping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp_n(value: int, soft: WakeupSoftBounds) -> int:
        lo = max(N_MIN, soft.n_min or N_MIN)
        hi = min(N_MAX, soft.n_max or N_MAX)
        return max(lo, min(hi, value))

    @staticmethod
    def _clamp_t(value: int, soft: WakeupSoftBounds) -> int:
        lo = max(T_MINUTES_MIN, soft.t_minutes_min or T_MINUTES_MIN)
        hi = min(T_MINUTES_MAX, soft.t_minutes_max or T_MINUTES_MAX)
        return max(lo, min(hi, value))

    @staticmethod
    def _parse_soft_bounds(agent: Agent) -> WakeupSoftBounds:
        raw = agent.wakeup_config.get("soft_bounds") if agent.wakeup_config else None
        if not isinstance(raw, dict):
            return WakeupSoftBounds()
        return WakeupSoftBounds(
            n_min=raw.get("n_min"),
            n_max=raw.get("n_max"),
            t_minutes_min=raw.get("t_minutes_min"),
            t_minutes_max=raw.get("t_minutes_max"),
        )


__all__ = ["WakeupService"]
