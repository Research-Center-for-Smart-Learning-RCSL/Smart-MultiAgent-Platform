"""Wake-up evaluator & trigger service (G.3 + G.4 + G.5).

Responsibilities:
- Evaluate whether a wake-up trigger should fire for a given binding.
- Handle ``message.created`` events from the conversation context.
- Handle presence changes to pause/resume silence timers (R15.05b).

Every decision reads the binding's effective config through
``RoomWakeupService`` ([R15.09]), which also owns the agent's self-modification
tool, the room's human settings and the periodic refresh.

SoC:
- Layer resolution → ``domain.room_wakeup``
- Redis counters/timers → ``infrastructure.wakeup_state`` (binding-scoped)
- Binding layers → ``ConversationFacade``; agent defaults → ``AgentsFacade``
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.interfaces.facade import Agent
from contexts.conversation.infrastructure.presence import PresenceTracker
from contexts.conversation.interfaces.facade import (
    HEADCOUNT_PRESENCE_LIMIT,
    BindingWakeupState,
    ChatroomAgentRole,
    ConversationFacade,
)
from contexts.orchestration.application.room_wakeup_service import RoomWakeupService
from contexts.orchestration.domain.models import ON_JOIN_COALESCE_SECONDS
from contexts.orchestration.infrastructure import wakeup_state
from contexts.orchestration.infrastructure.metrics import WAKEUP_FIRES
from shared_kernel import audit
from shared_kernel.auth.clients import now

logger = logging.getLogger(__name__)


class WakeTarget(NamedTuple):
    """An agent to wake, and the binding incarnation that decided it ([R15.09])."""

    agent_id: uuid.UUID
    binding_id: uuid.UUID


class PlatformWake(NamedTuple):
    """Platform-claimed wakes and the ``[Room notice]`` each turn is given."""

    targets: list[WakeTarget]
    notice: str


def class_start_notice(connected: int) -> str:
    return f"The class is starting: {connected} participants are connected."


class WakeupService:
    """Application-level wake-up trigger evaluator (G.3)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._room_wakeup = RoomWakeupService(db)
        self._conversation = ConversationFacade(db)
        self._presence = PresenceTracker()

    # ------------------------------------------------------------------
    # Event: message.created in a room
    # ------------------------------------------------------------------

    async def on_message_created(
        self,
        *,
        room_id: uuid.UUID,
        sender_is_user: bool,
        sender_agent_id: uuid.UUID | None = None,
        agent_ids: list[uuid.UUID],
        observer_agent_ids: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset(),
        sender_is_human: bool | None = None,
        states: Sequence[BindingWakeupState] | None = None,
    ) -> list[WakeTarget]:
        """Called when any message is created in a room.

        ``agent_ids`` is the set of agents bound to this room. Returns the agents
        that should wake, each with the binding incarnation that fired.

        Evaluates every_n_messages trigger (R15.01/Q49: counts all messages in
        the room — user + agent) and resets autostop only on user messages.
        Loop protection is the job of ``autostop_rounds`` (R15.03/R15.04).

        One carve-out ([R15.02]): a service-authored ``activity_submission`` echo
        re-arms the silence clock without reaching this method at all — see
        ``triggers.evaluate_room_activity``. It is room activity, but not a turn
        the room is waiting on, so it neither counts toward ``every_n_messages``
        nor resets the autostop cap.

        ``sender_agent_id`` — when set, the author is excluded from its own
        wake list (an agent must not be woken by its own reply).

        ``observer_agent_ids`` — observer-role bindings (O-2/R28.04): their
        output is out-of-band, so the empty-room presence gate does not apply
        and the gated-wakeup owner bell must never fire for them — it would
        name the observer to non-creators (R28.09).

        ``sender_is_human`` — whether a user or guest wrote it, which is what
        ``count: "human"`` counts ([R15.01]). Defaults to ``sender_is_user``; the
        released-observation path passes False explicitly, because it resets
        autostop like a user message but is agent-authored.

        ``states`` — the room's binding layers when the caller already read them
        with its binding rows; read here otherwise.
        """
        human = sender_is_user if sender_is_human is None else sender_is_human
        wake_list: list[WakeTarget] = []
        if states is None:
            states = await self._conversation.room_binding_wakeup_states(room_id)
        by_agent = {s.agent_id: s for s in states}

        for agent_id in agent_ids:
            # One agent's evaluation failing (a bad facade read, a Redis hiccup) must
            # not drop every other bound agent's wake-up (F-2 / AC-1): the loop used to
            # have no per-agent guard, so a single exception discarded `wake_list` for
            # the whole room. Mirrors the per-item guard in `evaluate_silence`
            # (app/workers/tasks/orchestration.py).
            try:
                state = by_agent.get(agent_id)
                current = await self._room_wakeup.for_state(state) if state is not None else None
                if state is None or current is None:
                    continue
                agent, binding = current.agent, state.binding_id

                cfg = current.effective.config
                if cfg.is_inert() or cfg.triggers.call_only.enabled:
                    continue

                await wakeup_state.touch_silence_timestamp(agent_id, room_id, binding)

                if sender_is_user:
                    await wakeup_state.reset_autostop(agent_id, room_id, binding)

                every_n = cfg.triggers.every_n_messages
                if every_n.enabled and (every_n.count == "all" or human):
                    count = await wakeup_state.increment_message_count(agent_id, room_id, binding)
                    n = every_n.n
                    due = n > 0 and count % n == 0
                    if agent_id == sender_agent_id:
                        # [R15.01a]: the N-th message is this agent's own. Deferred to
                        # the next message from anyone else, not dropped.
                        if due:
                            await wakeup_state.set_fire_due(agent_id, room_id, binding)
                        continue
                    # Consumed on every other sender's message, due or not: a flag
                    # left behind by a firing that happened anyway would fire twice.
                    if await wakeup_state.pop_fire_due(agent_id, room_id, binding):
                        due = True
                    if due:
                        # APP-3: `not sender_is_user` is load-bearing. The roster is
                        # the *WebSocket* roster, and a message posted over REST
                        # never touches it -- a user whose socket is still
                        # handshaking (the first message in a freshly created room)
                        # or mid-reconnect reads as absent. The triggering message
                        # is itself proof the room is not empty, so the gate applies
                        # only to agent-authored messages, where nothing has
                        # demonstrated a human is watching.
                        if (
                            agent_id not in observer_agent_ids
                            and not cfg.allow_self_open
                            and not sender_is_user
                        ):
                            members = await self._presence.list_room(room_id)
                            if not members:
                                await self._notify_wakeup_gated(agent, room_id)
                                continue
                        WAKEUP_FIRES.labels(kind="every_n_messages").inc()
                        wake_list.append(WakeTarget(agent_id, binding))
            except Exception:
                await self._db.rollback()
                logger.warning(
                    "on_message_created: wake-up evaluation failed agent=%s room=%s",
                    agent_id,
                    room_id,
                    exc_info=True,
                )

        return wake_list

    async def _notify_wakeup_gated(self, agent: Agent, room_id: uuid.UUID) -> None:
        """Tell the project's owners that ``agent``'s ``every_n_messages`` wake-up
        was suppressed because nobody was present and ``allow_self_open`` is off.

        Debounced to at most once an hour per (agent, room) so an active room
        cannot spam the bell. Self-contained best-effort: a failure here must not
        disturb the wake-up dispatch that called it.

        Each owner is committed individually. ``NotificationService`` publishes
        ``notification.created`` on flush, so committing per owner keeps every
        published bell entry backed by a durable row — a later owner's failure
        can't leave an earlier owner with a ghost notification. If nothing was
        delivered, the debounce token is released so the next message retries
        rather than the room going dark for a full hour.
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

        delivered = 0
        try:
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
                delivered += 1
        except Exception:
            await self._db.rollback()
            if delivered == 0:
                # Nothing reached anyone — free the debounce token so the next
                # gated message can retry instead of staying silent for an hour.
                await wakeup_state.release_gated_notice(agent.id, room_id)
            logger.warning("presence-gate notice failed agent=%s room=%s", agent.id, room_id, exc_info=True)

    # ------------------------------------------------------------------
    # Silence timer evaluation (called periodically per agent+room)
    # ------------------------------------------------------------------

    async def evaluate_silence_trigger(self, state: BindingWakeupState, agent: Agent | None = None) -> bool:
        """Check if silence_minutes trigger should fire (R15.02).

        Returns True if the binding should wake up due to silence.

        An observer binding (O-2/R28.04) skips the live-roster presence gate:
        observer output never enters the room, so an observer keeps observing
        rooms nobody is watching.
        """
        current = await self._room_wakeup.for_state(state, agent)
        if current is None:
            return False
        agent_id, room_id, binding = state.agent_id, state.chatroom_id, state.binding_id
        is_observer = current.is_observer

        cfg = current.effective.config
        if not cfg.triggers.silence_minutes.enabled:
            return False
        if cfg.triggers.call_only.enabled:
            return False

        last_ts = await wakeup_state.get_silence_timestamp(agent_id, room_id, binding)
        if last_ts is None:
            await wakeup_state.touch_silence_timestamp(agent_id, room_id, binding)
            return False

        elapsed_minutes = (datetime.now(UTC) - last_ts).total_seconds() / 60.0
        if elapsed_minutes < cfg.triggers.silence_minutes.t_minutes:
            return False

        # [R15.02]: someone typed within the composing window, so the room is not
        # silent, only quiet; re-arm instead of interrupting them mid-sentence.
        # Observers included: a lull that is not one is not worth reporting.
        if await self._presence.composing_recently(room_id):
            await wakeup_state.touch_silence_timestamp(agent_id, room_id, binding)
            return False

        # Check autostop (R15.03 / R15.04); observers use their own cap (O-3/R28.12).
        autostop_limit = cfg.triggers.silence_minutes.autostop_limit_for(is_observer=is_observer)
        autostop_count = await wakeup_state.get_autostop_count(agent_id, room_id, binding)
        if autostop_count >= autostop_limit:
            return False

        # R15.05b: the live roster is authoritative and independent of
        # allow_self_open, which gates only the every-N trigger.
        if not is_observer:
            members = await self._presence.list_room(room_id)
            if not members:
                return False

        WAKEUP_FIRES.labels(kind="silence_minutes").inc()
        return True

    # ------------------------------------------------------------------
    # Presence change: re-arm the silence timer on join (R15.05b)
    # ------------------------------------------------------------------

    async def on_users_present(
        self,
        *,
        room_id: uuid.UUID,
        agent_ids: list[uuid.UUID],
    ) -> None:
        """Called when a user joins a room, re-arming the silence timer.

        The pause half of this hook was retired
        (2026-07-27-wakeup-sweep-failure-isolation C2): the live roster read
        in `evaluate_silence_trigger` is now the sole, level-triggered
        authority on an empty room, so there is nothing left to pause here.
        """
        wanted = set(agent_ids)
        for state in await self._conversation.room_binding_wakeup_states(room_id):
            if state.agent_id in wanted:
                await wakeup_state.touch_silence_timestamp(state.agent_id, room_id, state.binding_id)

    async def on_activation_changed(self, *, room_id: uuid.UUID) -> None:
        """An activity round started or ended: reset every binding's autostop ([R15.03]).

        A round transition is a new stage of the class, not more of the same agent-only
        exchange the cap exists to stop. Without this, a class no human speaks in
        (the autonomous teacher of phase 4) stops being woken ``autostop_rounds`` turns
        after its last human message, which strands every later stage that only a
        silence wake can open.

        Bounded the same way a human message bounds it: only a real transition reaches
        here (a repeated start writes no notice), and silence wakes still need a
        connected human ([R15.05b]).
        """
        for state in await self._conversation.room_binding_wakeup_states(room_id):
            await wakeup_state.reset_autostop(state.agent_id, room_id, state.binding_id)

    # ------------------------------------------------------------------
    # Arrival: the on_join trigger ([R15.26])
    # ------------------------------------------------------------------

    async def on_participant_joined(self, *, room_id: uuid.UUID, principal_id: uuid.UUID) -> list[WakeTarget]:
        """The bindings that owe one ``on_join`` wake for this arrival.

        Only the first arrival of a coalescing window returns a binding; the
        caller defers its wake to the end of the window, so later arrivals in the
        same minute are in that turn's transcript. The room creator's own arrival
        wakes nothing: agents are never shown it ([R13.79]), so the turn would have
        no arrival to act on.
        """
        room = await self._conversation.get_chatroom(room_id)
        if room is None or room.created_by_user_id == principal_id:
            return []
        has_messages: bool | None = None
        targets: list[WakeTarget] = []
        for state in await self._conversation.room_binding_wakeup_states(room_id):
            try:
                current = await self._room_wakeup.for_state(state)
                if current is None or current.is_observer:
                    continue
                cfg = current.effective.config
                if not cfg.triggers.on_join.enabled or cfg.triggers.call_only.enabled:
                    continue
                if not cfg.allow_self_open:
                    # [R15.05]: nothing said yet means nothing to orient anyone to.
                    if has_messages is None:
                        has_messages = await self._conversation.room_has_messages(room_id)
                    if not has_messages:
                        continue
                if await wakeup_state.claim_join_batch(
                    state.agent_id, room_id, state.binding_id, ON_JOIN_COALESCE_SECONDS
                ):
                    WAKEUP_FIRES.labels(kind="on_join").inc()
                    targets.append(WakeTarget(state.agent_id, state.binding_id))
            except Exception:
                await self._db.rollback()
                logger.warning(
                    "on_participant_joined: evaluation failed agent=%s room=%s",
                    state.agent_id,
                    room_id,
                    exc_info=True,
                )
        return targets

    # ------------------------------------------------------------------
    # Platform wakes: class start ([R15.27]) and activity facts ([R15.28])
    # ------------------------------------------------------------------

    async def on_class_start_check(
        self, *, room_id: uuid.UUID, arriving_principal_id: uuid.UUID | None = None
    ) -> PlatformWake | None:
        """Open the room's class once, when enough students are connected or the
        first has waited ``max_wait_minutes``.

        Called on every connection, by the sweep (which opens a class on the max
        wait with nobody new arriving) and when the room creator changes or
        resets the setting. Unavailable presence is no signal: a class never
        opens on a count it could not read. The room creator is never counted
        and never starts the wait on their own. The wait starts at the first
        check that sees a student connected, so a class enabled or reset while
        students are already present still opens on the max wait; a room
        everyone left stops waiting until the next student arrives.
        """
        state = await self._conversation.class_start_state(room_id)
        if state is None or not state.armed or state.config is None:
            return None
        room = await self._conversation.get_chatroom(room_id)
        if room is None:
            return None
        first_arrival = state.first_arrival_at
        if (
            first_arrival is None
            and arriving_principal_id is not None
            and arriving_principal_id != room.created_by_user_id
        ):
            # Before the presence read, so an outage on the arrival still puts
            # the room in the sweep, which retries the count.
            await self._conversation.record_class_first_arrival(room_id)
            first_arrival = now()
        snapshot = await self._conversation.room_presence_snapshot(room_id, limit=HEADCOUNT_PRESENCE_LIMIT)
        if not snapshot.available:
            return None
        connected = len({u for u in snapshot.user_ids if u != room.created_by_user_id})
        if connected == 0:
            if first_arrival is not None:
                await self._conversation.clear_class_first_arrival(room_id)
            return None
        if first_arrival is None:
            await self._conversation.record_class_first_arrival(room_id)
            first_arrival = now()
        config = state.config
        if connected >= config.min_participants:
            reason = "min_participants"
        elif first_arrival is not None and now() - first_arrival >= timedelta(
            minutes=config.max_wait_minutes
        ):
            reason = "max_wait"
        else:
            return None
        if not await self._conversation.claim_class_start(room_id):
            return None
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.class_started",
                resource_type="chatroom",
                resource_id=room_id,
                metadata={"connected": str(connected), "reason": reason},
            ),
        )
        targets: list[WakeTarget] = []
        for binding in await self._conversation.room_binding_wakeup_states(room_id):
            current = await self._room_wakeup.for_state(binding)
            if current is None or current.is_observer:
                continue
            triggers = current.effective.config.triggers
            if triggers.class_start.enabled and not triggers.call_only.enabled:
                WAKEUP_FIRES.labels(kind="class_start").inc()
                targets.append(WakeTarget(binding.agent_id, binding.binding_id))
        return PlatformWake(targets=targets, notice=class_start_notice(connected))

    async def granted_targets(self, *, room_id: uuid.UUID, activity_type_id: uuid.UUID) -> list[WakeTarget]:
        """The bindings an activity fact wakes: those whose live grant covers its type.

        The grant is the teacher's delegation of this activity to the agent
        ([R30.37]); an agent the teacher did not trust to end it is not woken to.
        """
        targets: list[WakeTarget] = []
        for binding in await self._conversation.room_binding_wakeup_states(room_id):
            current = await self._room_wakeup.for_state(binding)
            if current is None or current.is_observer or current.effective.config.triggers.call_only.enabled:
                continue
            grant = await self._conversation.activity_control_grant(
                chatroom_id=room_id, agent_id=binding.agent_id
            )
            if grant is not None and activity_type_id in grant.activity_type_ids:
                targets.append(WakeTarget(binding.agent_id, binding.binding_id))
        return targets

    # ------------------------------------------------------------------
    # Agent spoke without user reply → bump autostop
    # ------------------------------------------------------------------

    async def on_agent_message_sent(
        self,
        *,
        agent_id: uuid.UUID,
        room_id: uuid.UUID,
        binding_id: uuid.UUID | None = None,
    ) -> int:
        """Track consecutive agent-only rounds for autostop (R15.03).

        The round is counted on ``binding_id``, the incarnation the turn ran for,
        so a rebind during the turn starts its own count at zero. Without one the
        live binding is used; 0 when the agent is no longer bound.

        A turn of the agent running the class is not counted (see
        :meth:`_runs_the_class`); the count it returns is then 0.
        """
        if await self._runs_the_class(agent_id=agent_id, room_id=room_id):
            return 0
        if binding_id is None:
            state = await self._conversation.binding_wakeup_state(chatroom_id=room_id, agent_id=agent_id)
            if state is None:
                return 0
            binding_id = state.binding_id
        return await wakeup_state.increment_autostop(agent_id, room_id, binding_id)

    async def _runs_the_class(self, *, agent_id: uuid.UUID, room_id: uuid.UUID) -> bool:
        """[R15.03]: the agent holds activity control and a student is connected.

        Such an agent is teaching, not in the agent-only exchange the cap exists to
        stop: in an autonomous class the students work quietly while the teacher alone
        moves the class on, and every stage after the cap would otherwise wait for a
        student to speak. A connected student bounds it as a human message does: the
        silence trigger never wakes the agent into a room with nobody there, and the
        room creator watching is not a class. Observers are excluded because their
        output never reaches the room. Any failure counts the round.
        """
        try:
            grant = await self._conversation.activity_control_grant(chatroom_id=room_id, agent_id=agent_id)
            if grant is None or not grant.activity_type_ids:
                return False
            state = await self._conversation.binding_wakeup_state(chatroom_id=room_id, agent_id=agent_id)
            if state is None or state.role is ChatroomAgentRole.OBSERVER:
                return False
            room = await self._conversation.get_chatroom(room_id)
            if room is None:
                return False
            snapshot = await self._conversation.room_presence_snapshot(
                room_id, limit=HEADCOUNT_PRESENCE_LIMIT
            )
            return snapshot.available and any(u != room.created_by_user_id for u in snapshot.user_ids)
        except Exception:
            await self._db.rollback()
            logger.warning(
                "class check failed agent=%s room=%s; counting the round", agent_id, room_id, exc_info=True
            )
            return False


__all__ = ["PlatformWake", "WakeTarget", "WakeupService", "class_start_notice"]
