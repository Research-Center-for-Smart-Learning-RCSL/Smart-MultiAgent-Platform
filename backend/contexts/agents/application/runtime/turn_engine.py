"""Agent turn engine (K.2) — the core of the agent runtime.

`TurnEngine.run_turn` produces a streamed agent reply to a chatroom: acquire a
per-(agent, chatroom) lock, load the agent, resolve its prompt, assemble the
model-facing history (compacting if configured), inject RAG context, stream the
provider call through the K.1 router, run tool-use rounds, persist the reply as
a ``sender_type=AGENT`` message, and emit ``agent.thinking/token/finished`` on
the chatroom channel (R13.19).

Runs in the **arq worker only** (never the web process): provider calls and the
turn lock assume a long-lived background context. The triggers that invoke this
(message / wakeup / A2A) are wired in K.3.
"""

from __future__ import annotations

import contextlib
import enum
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from prometheus_client import Counter, Histogram
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.activity_context_provider import ActivityContextProvider
from contexts.agents.application import context as ctxmod
from contexts.agents.application.prompt_loader import LazyPrompt, SectionCache, assemble
from contexts.agents.application.runtime import model_attachments as mattach
from contexts.agents.application.runtime import transcript as tx
from contexts.agents.application.runtime.summariser import RouterSummariser
from contexts.agents.application.runtime.tool_registry import (
    Tool,
    build_cast_approval_vote_tool,
    build_registry,
)
from contexts.agents.domain.models import CONTEXT_LIMITS, DEFAULT_CHAT_MODELS, Agent
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.agents.infrastructure.turn_lock import turn_lock
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.application.message_service import MessageService
from contexts.conversation.application.observation_service import ObservationService
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ObservationRepository,
)
from contexts.conversation.interfaces import emit_agent_finished_error, room_channel
from contexts.conversation.interfaces.facade import ConversationFacade, MessageAttachment
from contexts.identity.interfaces import user_channel
from contexts.identity.interfaces.facade import IdentityFacade
from contexts.keys.application.provider_router import (
    ProviderRequest,
    ProviderRouter,
    ProviderStreamError,
    StreamComplete,
    TokenDelta,
)
from contexts.keys.domain.errors import KeyGroupExhausted
from contexts.keys.domain.providers import ProviderCapability
from contexts.keys.infrastructure.adapters import build_router
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.knowledge.application.graphrag_context_provider import (
    GraphRagContextProvider,
    build_evidence_fetcher,
)
from contexts.knowledge.application.knowmap_context_provider import (
    KnowledgeMapContextProvider,
)
from contexts.knowledge.application.rag_context_provider import RagContext, RagContextProvider
from shared_kernel import audit
from shared_kernel.observability.metrics import REGISTRY
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

CancelCheck = Callable[[], Awaitable[bool]]

MAX_TOOL_ROUNDS = 8
_DEFAULT_MAX_TOKENS = 4096
# F-16: token budget the allocator reserves for each of the two small graph
# blocks (Concept Map, Knowledge Map) before File RAG takes the remainder. Sized
# to the graph blocks' existing 2 KB byte cap (~700 tokens of Latin text).
_GRAPH_BLOCK_TOKEN_BUDGET = 700
# Fraction shaved off the knowledge budget to absorb the coarse estimator's
# under-count (estimate_tokens is heuristic; a tokenizer-backed estimator is FU).
_KNOWLEDGE_SAFETY_MARGIN = 0.1


def _sampling_payload(agent: Agent) -> dict[str, Any]:
    """Provider-payload fragment carrying the agent's set sampling controls.

    Only non-None controls are included so unset ones preserve provider
    defaults; each adapter then applies its own constraint (OpenAI drops
    temperature for reasoning models, Claude drops it on newer generations,
    seed is forwarded only where the provider supports it).
    """
    return {
        k: v
        for k, v in (
            ("temperature", agent.temperature),
            ("top_p", agent.top_p),
            ("seed", agent.seed),
        )
        if v is not None
    }


_HISTORY_RESUME_NOTE = "[Conversation resumes; earlier turns were summarized in the system prompt.]"

# R28.05 — how many of the observer's own past observations fold into its
# system context so successive analyses build on each other.
OBSERVER_MEMORY_WINDOW = 10

# R28.01 — fixed framing for observer turns. Code-side (not user-editable
# prompt text) so an observer never addresses the room as if it could reply.
_OBSERVER_SYSTEM_NOTE = (
    "[Observer role]\n"
    "You are a silent observer of this conversation. Your reply is delivered "
    "privately to the room owner as an analysis; the participants cannot see "
    "it. Do not address the participants directly."
)

# Per-(agent, room) turn rate limit — backstop against trigger storms. Not yet
# surfaced in `settings.limits` (no agent-runtime settings section exists);
# promote these to settings when one lands.
_TURN_RATE_WINDOW_S = 300
_TURN_RATE_MAX_TURNS = 30

# Code-Interpreter workspace staging caps — how many of the triggering user
# message's attachments to copy into the kernel, and the total byte budget.
_MAX_STAGED_FILES = 10
_MAX_STAGED_BYTES = 64 * 1024 * 1024
_MAX_AGENT_FILES_BYTES = 128 * 1024 * 1024
_MAX_KNOWLEDGE_QUERIES = 3
_MAX_KNOWLEDGE_QUERY_CHARS = 1200

# ---- Turn observability (same pattern as PROVIDER_CALL_TOTAL) ---------------

AGENT_TURNS_TOTAL = Counter(
    "agent_turns_total",
    "Agent turns run by the turn engine, labelled by terminal result.",
    labelnames=("result",),
    registry=REGISTRY,
)

AGENT_TURN_DURATION_SECONDS = Histogram(
    "agent_turn_duration_seconds",
    "Wall-clock duration of one agent turn (lock acquire to release).",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    registry=REGISTRY,
)

AGENT_STREAM_TOKENS_TOTAL = Counter(
    "agent_stream_tokens_total",
    "Streamed token deltas emitted by agent turns.",
    registry=REGISTRY,
)

# Per-provider chat-model defaults — passed to the adapter as a ``models`` map.
# When an agent has a ``model_id`` configured, ``_resolve_models`` overrides
# the entry for that provider so the adapter uses the agent's chosen model.
# Sourced from the agents domain so the runtime default and the default the
# model-catalog API advertises to the UI stay in lock-step.
_DEFAULT_CHAT_MODELS: dict[str, str] = dict(DEFAULT_CHAT_MODELS)
_CONTEXT_LIMITS: dict[str, int] = dict(CONTEXT_LIMITS)

# Appended to the system prompt whenever history carries sender labels. The
# provider sees other participants' turns as "Name: message"; without this note
# the model tends to mirror the convention and prefix its own reply with its name.
_PARTICIPANT_LABEL_NOTE = (
    "Messages from other participants are prefixed with the speaker's name as "
    '"Name: message". Use these names to tell participants apart. When you reply, '
    "write only your own message content -- never prefix it with your own name."
)


def _resolve_models(agent: Agent) -> dict[str, str]:
    """Build the ``models`` map for a turn, applying agent-level overrides."""
    models = dict(_DEFAULT_CHAT_MODELS)
    if agent.model_id:
        models[agent.model_hint.value] = agent.model_id
    return models


# Must exceed any realistic heartbeat-extended turn; the flag is popped
# after every turn so it never lingers under normal operation.
_QUEUED_TRIGGER_TTL_S = 3600


def _queued_trigger_key(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"turn:queued:{agent_id}:{chatroom_id}"


def _queued_trigger_message_key(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"turn:queued:msg:{agent_id}:{chatroom_id}"


async def _mark_trigger_queued(
    agent_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    trigger: str,
    trigger_message_id: uuid.UUID | None = None,
) -> None:
    """Record (at most once — SETNX) that a trigger landed while a turn held
    the lock, so the lock holder re-enqueues exactly one follow-up turn.

    The triggering message id is tracked in a separate key with last-write-wins
    semantics (plain SET, not NX): when several messages coalesce into the one
    follow-up turn, the most recently arrived id is the most relevant anchor
    for attachment resolution — matching how an uncontended turn would resolve
    against whatever is currently latest."""
    try:
        from shared_kernel.auth.clients import get_redis

        redis = get_redis()
        await redis.set(
            _queued_trigger_key(agent_id, chatroom_id),
            trigger,
            nx=True,
            ex=_QUEUED_TRIGGER_TTL_S,
        )
        if trigger_message_id is not None:
            await redis.set(
                _queued_trigger_message_key(agent_id, chatroom_id),
                str(trigger_message_id),
                ex=_QUEUED_TRIGGER_TTL_S,
            )
    except Exception:
        _log.warning(
            "failed to queue coalesced trigger agent=%s room=%s",
            agent_id,
            chatroom_id,
            exc_info=True,
        )


async def _pop_queued_trigger(
    agent_id: uuid.UUID, chatroom_id: uuid.UUID
) -> tuple[str, uuid.UUID | None] | None:
    """Atomically read-and-clear the coalesced-trigger flag (GETDEL) and its
    associated (last-write-wins) triggering message id, if any."""
    try:
        from shared_kernel.auth.clients import get_redis

        redis = get_redis()
        val = await redis.getdel(_queued_trigger_key(agent_id, chatroom_id))
        mid_raw = await redis.getdel(_queued_trigger_message_key(agent_id, chatroom_id))
    except Exception:
        _log.warning(
            "failed to read coalesced trigger agent=%s room=%s",
            agent_id,
            chatroom_id,
            exc_info=True,
        )
        return None
    if not val:
        return None
    trigger = val.decode() if isinstance(val, bytes) else str(val)
    message_id: uuid.UUID | None = None
    if mid_raw:
        mid_str = mid_raw.decode() if isinstance(mid_raw, bytes) else str(mid_raw)
        try:
            message_id = uuid.UUID(mid_str)
        except ValueError:
            message_id = None
    return trigger, message_id


@dataclass(frozen=True, slots=True)
class TurnResult:
    status: str  # "completed" | "skipped" | "failed"
    reason: str | None = None
    message_id: uuid.UUID | None = None
    text: str = ""
    tool_rounds: int = 0


class _TurnCancelled(Exception):
    """Raised by _stream_with_tools when a cancel_check fires."""

    def __init__(self, rounds_completed: int) -> None:
        self.rounds_completed = rounds_completed
        super().__init__(f"turn cancelled after {rounds_completed} rounds")


class _KnowledgeStarved(Exception):
    """Raised from request assembly when the knowledge budget floors at 0 while
    the agent actually has a knowledge source bound.

    Carries the two terms that produced the floor, because the handler cannot
    re-derive them: ``fixed_context`` is computed inside the assembly closure and
    a reader needs it to tell a too-low cap apart from one oversized message.
    """

    def __init__(self, *, fixed_context: int, ceiling: int) -> None:
        self.fixed_context = fixed_context
        self.ceiling = ceiling
        super().__init__(f"knowledge budget floored: fixed_context={fixed_context} ceiling={ceiling}")


class _BlockRole(enum.Enum):
    """How a system block participates in measurement vs rendering.

    Measurement and rendering deliberately disagree, and that asymmetry is the
    point: a block may be counted but not shown (conservative estimate) or shown
    but not counted (it is what the knowledge budget buys). Declaring the role
    once per block is what replaces two hand-synchronised functions -- the shape
    that produced F-16 and F-17.
    """

    MEASURED_AND_RENDERED = "measured_and_rendered"
    # Counted every turn, rendered only when history warrants it. Counting it
    # unconditionally keeps the estimate an over-count, never an under-count.
    MEASURED_ONLY = "measured_only"
    # Rendered but never counted: the knowledge blocks are the budget's output,
    # so counting them against it would be circular.
    RENDERED_ONLY = "rendered_only"


class _BlockSlot(enum.Enum):
    """Blocks whose text is supplied per call rather than fixed for the turn."""

    SUMMARIES = "summaries"
    KNOWLEDGE = "knowledge"


@dataclass(frozen=True, slots=True)
class _SystemBlock:
    name: str
    role: _BlockRole
    # Fixed text resolved at turn start; None means the block is absent this turn.
    text: str | None = None
    # Set instead of `text` when the content varies per call (summaries depend on
    # the history being assembled, which differs between the initial pass and the
    # recompaction pass).
    slot: _BlockSlot | None = None


@dataclass(frozen=True, slots=True)
class _SystemBlocks:
    """The turn's system blocks as one ordered list with explicit per-block roles.

    Replaces the hand-maintained ``_fixed_system_text`` / ``system_parts`` pair:
    order and role are declared once here, so measure and render cannot disagree
    about *which* blocks exist -- only about the ones whose role says they should.
    """

    blocks: tuple[_SystemBlock, ...]

    @classmethod
    def build(
        cls,
        *,
        base_system: str,
        is_observer: bool,
        memory_block: str | None,
        activity_block: str | None,
        staged_note: str | None,
        notify_block: str | None,
    ) -> _SystemBlocks:
        blocks: list[_SystemBlock] = [
            _SystemBlock("base_system", _BlockRole.MEASURED_AND_RENDERED, text=base_system)
        ]
        if is_observer:
            # R28.01 framing + R28.05 self-memory.
            blocks.append(
                _SystemBlock("observer_note", _BlockRole.MEASURED_AND_RENDERED, text=_OBSERVER_SYSTEM_NOTE)
            )
            blocks.append(_SystemBlock("memory", _BlockRole.MEASURED_AND_RENDERED, text=memory_block))
        blocks.append(_SystemBlock("summaries", _BlockRole.MEASURED_AND_RENDERED, slot=_BlockSlot.SUMMARIES))
        blocks.append(_SystemBlock("knowledge", _BlockRole.RENDERED_ONLY, slot=_BlockSlot.KNOWLEDGE))
        blocks.append(_SystemBlock("activity", _BlockRole.MEASURED_AND_RENDERED, text=activity_block))
        blocks.append(_SystemBlock("staged", _BlockRole.MEASURED_AND_RENDERED, text=staged_note))
        blocks.append(_SystemBlock("notify", _BlockRole.MEASURED_AND_RENDERED, text=notify_block))
        blocks.append(
            _SystemBlock("participant_note", _BlockRole.MEASURED_ONLY, text=_PARTICIPANT_LABEL_NOTE)
        )
        return cls(blocks=tuple(blocks))

    @staticmethod
    def _texts(block: _SystemBlock, summaries: Sequence[str], knowledge_blocks: Sequence[str]) -> list[str]:
        if block.slot is _BlockSlot.SUMMARIES:
            return list(summaries)
        if block.slot is _BlockSlot.KNOWLEDGE:
            return list(knowledge_blocks)
        return [block.text] if block.text else []

    def measure(self, summaries: Sequence[str]) -> str:
        """The non-knowledge system text, for the compaction decision (F-17) and
        the knowledge budget (F-16). Knowledge blocks are excluded by role."""
        parts: list[str] = []
        for block in self.blocks:
            if block.role is _BlockRole.RENDERED_ONLY:
                continue
            parts.extend(self._texts(block, summaries, ()))
        return "\n\n".join(p for p in parts if p)

    def render(
        self,
        summaries: Sequence[str],
        knowledge_blocks: Sequence[str],
        *,
        include_participant_note: bool,
    ) -> str:
        """The system text actually sent to the provider."""
        parts: list[str] = []
        for block in self.blocks:
            if block.role is _BlockRole.MEASURED_ONLY and not include_participant_note:
                continue
            parts.extend(self._texts(block, summaries, knowledge_blocks))
        return "\n\n".join(p for p in parts if p)


class TurnEngine:
    def __init__(
        self,
        db: AsyncSession,
        *,
        router: ProviderRouter | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        bge_reranker_url: str | None = None,
    ) -> None:
        self._db = db
        self._router = router or build_router(db)
        self._qdrant_url = qdrant_url
        self._qdrant_api_key = qdrant_api_key
        self._rag_provider = RagContextProvider(
            db,
            router=self._router,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            bge_reranker_url=bge_reranker_url,
        )
        _conv_facade = ConversationFacade(db)
        self._graphrag_provider = GraphRagContextProvider(
            db,
            router=self._router,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            evidence_fetcher=build_evidence_fetcher(
                _conv_facade.get_message,
                _conv_facade.is_agent_in_chatroom,
            ),
        )
        # Axis-1 Knowledge Map (Phase 3, R11.14) — a third system block beside
        # file-RAG, independent of any Concept Map; built once per engine.
        self._knowmap_provider = KnowledgeMapContextProvider(
            db,
            router=self._router,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
        )
        # §30 (R30.15): recent structured activity events for an OBSERVER turn.
        # Coverage-gated (only present when the room has activities); built once.
        self._activity_provider = ActivityContextProvider(db)
        # Rooms whose one-shot POST /compact flag this engine consumed — used
        # to re-arm the flag if the turn that consumed it fails.
        self._compact_forced_rooms: set[uuid.UUID] = set()

    async def run_turn(
        self,
        *,
        agent_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        trigger: str,
        parent_agent_id: uuid.UUID | None = None,
        input_text: str | None = None,
        request_id: uuid.UUID | None = None,
        trigger_message_id: uuid.UUID | None = None,
    ) -> TurnResult:
        started = time.monotonic()
        result: TurnResult | None = None
        for attempt in range(2):
            async with turn_lock(agent_id, chatroom_id) as acquired:
                if acquired:
                    if attempt > 0:
                        # Acquired on retry — our mark from attempt 0 (or an
                        # earlier stranded one) may still be parked. Consume it
                        # now so the post-release pop doesn't re-enqueue a
                        # redundant follow-up for a trigger we're about to serve.
                        parked = await _pop_queued_trigger(agent_id, chatroom_id)
                        if parked is None:
                            # Someone else already popped our mark — that was
                            # the previous holder's post-release drain, which
                            # has already enqueued a follow-up turn for it.
                            # Running here too would duplicate that turn, so
                            # let the enqueued follow-up serve it instead.
                            break
                        trigger, parked_mid = parked
                        trigger_message_id = parked_mid or trigger_message_id
                    result = await self._run_locked(
                        agent_id=agent_id,
                        chatroom_id=chatroom_id,
                        trigger=trigger,
                        parent_agent_id=parent_agent_id,
                        input_text=input_text,
                        request_id=request_id,
                        trigger_message_id=trigger_message_id,
                    )
                    break
                await _mark_trigger_queued(agent_id, chatroom_id, trigger, trigger_message_id)
                # Re-check: the holder may have released AND popped before our
                # mark landed — if the lock is now free we take it; if still held
                # the holder's post-release pop sees our mark.
            if result is None and attempt == 0:
                continue
        if result is None:
            AGENT_TURNS_TOTAL.labels(result="skipped").inc()
            return TurnResult(status="skipped", reason="locked")
        # Lock released — drain the coalesced trigger (if any) into exactly one
        # follow-up wakeup so the message that arrived mid-turn gets a reply.
        queued = await _pop_queued_trigger(agent_id, chatroom_id)
        if queued is not None:
            queued_trigger, queued_message_id = queued
            try:
                from shared_kernel.queue import enqueue

                await enqueue(
                    "wakeup_agent",
                    str(agent_id),
                    str(chatroom_id),
                    queued_trigger,
                    str(queued_message_id) if queued_message_id else None,
                )
            except Exception:
                _log.warning(
                    "coalesced wakeup enqueue failed agent=%s room=%s",
                    agent_id,
                    chatroom_id,
                    exc_info=True,
                )
        AGENT_TURNS_TOTAL.labels(result=result.status).inc()
        AGENT_TURN_DURATION_SECONDS.observe(time.monotonic() - started)
        return result

    async def run_input_turn(
        self,
        *,
        agent_id: uuid.UUID,
        input_text: str,
        parent_agent_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        cancel_check: CancelCheck | None = None,
        chatroom_id: uuid.UUID | None = None,
    ) -> TurnResult:
        """Headless input→reply turn for A2A ``call`` / ``instruct`` (K.3 Pass 2).

        No room history, no reply persistence, no room binding check (the A2A
        scope check already authorised the caller), and no WS stream (there is no
        room subscriber). Still assembles the agent's per-invocation knowledge —
        File RAG and its attached Knowledge Map (R10.09/R11.14) — as system blocks
        before streaming. Drains any queued notifications so an approver agent can
        ``cast_approval_vote`` here. Returns the reply text in ``TurnResult.text``
        for the caller to put on the A2A reply envelope.

        ``chatroom_id`` — optional authoritative room. A2A envelopes carry none
        (Concept Maps never apply); the approval worker threads the room the vote
        is bound to, so the approver's room-scoped Concept Maps resolve for this
        turn. It must be a server-side room id, never a caller-supplied value.

        ``cancel_check`` — when set (A2A CALL turns only), checked at each tool
        round boundary; a True return stops the turn to save the user's provider
        spend when the caller has already timed out.
        """
        agent = await AgentsFacade(self._db).get_agent(agent_id)
        if agent is None:
            return TurnResult(status="skipped", reason="agent_gone")
        models = _resolve_models(agent)
        wf = str(workflow_run_id) if workflow_run_id else None
        pending_notes: list[dict[str, Any]] = []
        try:
            await self._audit(agent, None, "agent.turn_started", {"mode": "a2a", "workflow_run_id": wf})
            base_system, lazy_prompt, section_cache = self._resolve_prompt(agent)
            system_parts = [base_system] if base_system else []
            # Knowledge blocks precede the notify block, matching the room path's
            # order. With no history, retrieval keys off the current input alone.
            knowledge_queries = _knowledge_queries([], input_text=input_text)
            # A headless turn (e.g. an approval vote, F-15) may be threaded into a
            # room the agent does not belong to — the gate's chatroom_id can be an
            # arbitrary in-project room set by the workflow author. Room-scoped
            # Concept Maps inherit the room read-ACL, so resolve them only when the
            # agent is actually a member, granting no wider access than a normal
            # room turn for this agent+room would (R11.09 / F-15 AC-7). File RAG and
            # the Knowledge Map are the agent's own per-Agent bindings and stay
            # available regardless of room membership.
            knowledge_chatroom_id = chatroom_id
            if knowledge_chatroom_id is not None and not await ConversationFacade(
                self._db
            ).is_agent_in_chatroom(chatroom_id=knowledge_chatroom_id, agent_id=agent.id):
                knowledge_chatroom_id = None
            knowledge_blocks, _rag_ctx = await self._assemble_agent_knowledge(
                agent, knowledge_queries, chatroom_id=knowledge_chatroom_id
            )
            system_parts.extend(knowledge_blocks)
            notify_block, extra_tools, pending_notes = await self._pending_context_and_tools(agent, None)
            extra_tools = extra_tools + await self._builtin_tools(agent)
            if notify_block:
                system_parts.append(notify_block)
            system_text = "\n\n".join(p for p in system_parts if p)
            messages: list[dict[str, Any]] = [{"role": "user", "content": input_text}]
            registry = build_registry(
                self._db,
                agent_id=agent.id,
                lazy_prompt=lazy_prompt,
                section_cache=section_cache,
                extra=extra_tools,
            )
            await self._db.flush()
            final_text, rounds = await self._stream_with_tools(
                agent=agent,
                chatroom_id=None,
                parent_agent_id=parent_agent_id,
                system_text=system_text,
                messages=messages,
                models=models,
                registry=registry,
                room=None,
                cancel_check=cancel_check,
            )
            await self._audit(agent, None, "agent.turn_finished", {"mode": "a2a", "tool_rounds": rounds})
            await self._db.commit()
            return TurnResult(status="completed", text=final_text, tool_rounds=rounds)
        except _TurnCancelled as tc:
            _log.info("a2a turn cancelled agent=%s after %d rounds", agent_id, tc.rounds_completed)
            try:
                await self._audit(
                    agent,
                    None,
                    "agent.turn_cancelled",
                    {"mode": "a2a", "rounds_completed": tc.rounds_completed},
                )
                await self._db.commit()
            except Exception:
                _log.exception("a2a turn cancel-path bookkeeping failed")
            if tc.rounds_completed == 0:
                await self._requeue_notifications(agent, pending_notes)
            return TurnResult(status="skipped", reason="cancelled")
        except Exception as exc:
            _log.exception("a2a turn failed agent=%s", agent_id)
            await self._db.rollback()
            try:
                await self._audit(agent, None, "agent.turn_failed", {"mode": "a2a", "error": _err_kind(exc)})
                await self._db.commit()
            except Exception:
                _log.exception("a2a turn failure-path bookkeeping failed")
            await self._requeue_notifications(agent, pending_notes)
            return TurnResult(status="failed", reason=_err_kind(exc))

    async def _builtin_tools(
        self,
        agent: Agent,
        *,
        chatroom_id: uuid.UUID | None = None,
        artifact_sink: list[dict[str, Any]] | None = None,
    ) -> list[Tool]:
        """Assemble the sandbox (``code_exec`` / ``file``) + ``web_search`` built-in
        tools and the agent's bound MCP tools for this turn (K.5).

        ``chatroom_id`` routes ``code_exec`` to the room's persistent kernel and
        ``artifact_sink`` collects any artifacts it produces; both are ``None``
        for headless A2A turns (no room, no kernel, no artifact surface).

        Best-effort: a wiring fault (no Docker daemon in a dev run, etc.) must
        not abort the turn — the agent simply runs without those tools. Each
        tool's own ``invoke`` already degrades a runtime fault to an ``is_error``
        result, so this guard only covers assembly itself."""
        try:
            from contexts.agents.application.runtime.builtin_tools import (
                build_agent_tools,
                default_builtin_deps,
            )

            agent_tools = await AgentsFacade(self._db).list_agent_tools(agent.id)
            from dataclasses import replace as _replace

            deps = _replace(default_builtin_deps(), rag_provider=self._rag_provider)
            return build_agent_tools(
                self._db,
                agent=agent,
                tools=agent_tools,
                deps=deps,
                chatroom_id=chatroom_id,
                artifact_sink=artifact_sink,
            )
        except Exception:
            _log.warning("agent tool assembly failed for agent %s", agent.id, exc_info=True)
            return []

    async def _resolve_trigger_attachments(
        self, chatroom_id: uuid.UUID, trigger_message_id: uuid.UUID | None
    ) -> list[MessageAttachment]:
        """Resolve the triggering message's active attachments once per turn.

        Shared by ``_stage_workspace_inputs`` and ``_model_attachment_blocks``
        so both consumers see the same snapshot — two independent reads could
        otherwise observe different attachment status (e.g. an AV scan
        quarantining the file between the two calls), staging a file into the
        sandbox that the model-visible blocks then silently omit, or vice versa.
        """
        facade = ConversationFacade(self._db)
        if trigger_message_id is not None:
            return await facade.attachments_for_message(trigger_message_id)
        return await facade.latest_user_attachments(chatroom_id)

    async def _stage_workspace_inputs(
        self, agent: Agent, chatroom_id: uuid.UUID, attachments: list[MessageAttachment]
    ) -> str | None:
        """Stage the agent's persisted files and the triggering message's
        attachments into the code_exec workspace.

        Returns a one-line note listing the workspace paths (folded into the
        system prompt so the model knows where the files are) or ``None``.
        Best-effort and gated on ``code_exec`` actually being enabled — a fault
        here must never abort the turn."""
        try:
            from contexts.agents.domain.mcp import StagedFile
            from contexts.agents.domain.models import AgentToolType
            from contexts.agents.infrastructure.sandbox.docker_runsc import (
                docker_runsc_sandbox_from_settings,
            )

            agents_facade = AgentsFacade(self._db)
            agent_tools = await agents_facade.list_agent_tools(agent.id)
            if not any(
                t.enabled and t.tool_type == AgentToolType.HOSTED_CODE_INTERPRETER for t in agent_tools
            ):
                return None

            runner = docker_runsc_sandbox_from_settings()
            all_paths: list[str] = []

            # --- persisted agent workspace files (D.4) ---
            try:
                await self._stage_persisted_files(agent, runner, agents_facade, all_paths)
            except Exception:
                _log.warning("agent workspace file staging failed for %s", agent.id, exc_info=True)

            # --- triggering message's attachments (resolved by the caller) ---
            facade = ConversationFacade(self._db)
            if attachments:
                chosen: list[Any] = []
                total = 0
                for att in attachments[:_MAX_STAGED_FILES]:
                    if total + att.size_bytes > _MAX_STAGED_BYTES:
                        continue
                    total += att.size_bytes
                    chosen.append(att)
                if chosen:
                    blobs = await facade.read_attachments_bytes(chosen)
                    staged = [
                        StagedFile(filename=att.filename, data=data)
                        for att, data in zip(chosen, blobs, strict=True)
                        if data is not None
                    ]
                    if staged:
                        paths = await runner.stage_kernel_inputs(
                            agent_id=agent.id,
                            chatroom_id=chatroom_id,
                            files=staged,
                        )
                        all_paths.extend(paths)

            if not all_paths:
                return None
            return "[Files available in the code_exec workspace: " + ", ".join(all_paths) + "]"
        except Exception:
            _log.warning("workspace input staging failed for agent %s", agent.id, exc_info=True)
            return None

    async def _stage_persisted_files(
        self,
        agent: Agent,
        runner: Any,
        agents_facade: Any,
        out_paths: list[str],
    ) -> None:
        """Hydrate persisted workspace files into ``/workspace/agent-files/``."""
        import hashlib

        from contexts.agents.domain.mcp import StagedFile
        from shared_kernel.storage import get_minio_client

        ws_files = await agents_facade.list_workspace_files(agent.id)
        if not ws_files:
            return

        total = 0
        chosen = []
        for wf in ws_files:
            if total + wf.size_bytes > _MAX_AGENT_FILES_BYTES:
                break
            total += wf.size_bytes
            chosen.append(wf)
        if not chosen:
            return

        # The manifest is the cache key for what is on the volume, so it must
        # cover exactly the staged prefix. Hashing the whole set instead made the
        # key describe bytes that were never written: an edit past the size cut
        # invalidated the cache and re-staged an identical prefix, while the
        # manifest still claimed a file set the sandbox had never seen.
        manifest_sha = hashlib.sha256(
            "\n".join(sorted(f"{wf.path}:{wf.sha256}" for wf in chosen)).encode()
        ).hexdigest()

        storage = get_minio_client()
        bucket = storage._cfg.bucket_agent_workspace
        staged: list[StagedFile] = []
        for wf in chosen:
            data = await storage.get_object(bucket=bucket, key=wf.minio_key)
            staged.append(StagedFile(filename=wf.path.rsplit("/", 1)[-1], data=data))

        paths = await runner.stage_agent_workspace_files(
            agent_id=agent.id,
            files=staged,
            manifest_sha=manifest_sha,
        )
        out_paths.extend(paths)

    async def _persist_artifacts(
        self,
        agent: Agent,
        chatroom_id: uuid.UUID,
        message_id: uuid.UUID,
        artifacts: list[dict[str, Any]],
    ) -> int:
        """Store code_exec artifacts as agent-authored attachments bound to the
        reply. Best-effort and in its own transaction so a storage hiccup never
        rolls back the already-committed reply. Returns the count persisted."""
        if not artifacts:
            return 0
        import asyncio
        import base64

        from contexts.conversation.application.attachment_service import AttachmentService

        try:
            svc = AttachmentService(self._db)
            seen: set[str] = set()
            prepared: list[tuple[str, str, bytes]] = []
            for art in artifacts:
                rel = str(art.get("rel_path") or art.get("filename") or "")
                # Only dedup *named* artifacts. A blank key would otherwise make
                # the first unnamed artifact poison the set so every later
                # unnamed one (real charts/files) is silently dropped.
                if rel:
                    if rel in seen:
                        continue
                    seen.add(rel)
                b64 = art.get("b64")
                if not b64:
                    # Large artifact not inlined by the kernel — skipped in v1.
                    continue
                try:
                    data = base64.b64decode(b64)
                except Exception:
                    _log.debug("skipping artifact with undecodable b64", exc_info=True)
                    continue
                prepared.append(
                    (
                        str(art.get("filename") or "artifact"),
                        str(art.get("mime") or "application/octet-stream"),
                        data,
                    )
                )
            if not prepared:
                return 0
            # Uploads are independent object-store writes -> run concurrently;
            # the row inserts/bind that follow share the DB session, so they stay
            # sequential inside persist_agent_artifacts.
            uploads = await asyncio.gather(
                *[
                    svc.upload_agent_artifact(
                        project_id=agent.project_id, chatroom_id=chatroom_id, filename=fn, mime=mt, data=d
                    )
                    for (fn, mt, d) in prepared
                ]
            )
            count = await svc.persist_agent_artifacts(
                agent_id=agent.id, message_id=message_id, chatroom_id=chatroom_id, uploads=uploads
            )
            await self._db.commit()
            return count
        except Exception:
            _log.warning("artifact persistence failed for agent %s", agent.id, exc_info=True)
            with contextlib.suppress(Exception):
                await self._db.rollback()
            return 0

    def _resolve_prompt(self, agent: Agent) -> tuple[str, LazyPrompt | None, SectionCache | None]:
        """Resolve the agent's system prompt (R9.04–R9.08). All chat providers
        support tools, so ``lazy`` never has to fall back to ``full`` here."""
        prompt = assemble(
            agent.system_prompt,
            strategy=agent.prompt_strategy.value,
            provider_supports_tools=True,
        )
        lazy_prompt: LazyPrompt | None = prompt if isinstance(prompt, LazyPrompt) else None
        section_cache = SectionCache() if lazy_prompt is not None else None
        base_system = lazy_prompt.index if lazy_prompt is not None else prompt.text  # type: ignore[union-attr]
        return base_system, lazy_prompt, section_cache

    async def _pending_context_and_tools(
        self, agent: Agent, chatroom_id: uuid.UUID | None
    ) -> tuple[str | None, list[Tool], list[dict[str, Any]]]:
        """Drain queued A2A notifications for this agent into a context block
        (R9.16) and, for approval-request notifications, the ``cast_approval_vote``
        tool scoped to exactly the pending gate ids. Best-effort: a Redis hiccup
        yields no context rather than failing the turn.

        ``chatroom_id`` — the room this turn is running in (``None`` for a
        headless A2A turn). ``pending_notify`` is keyed only by agent id, not
        by room, so a ``released_observation`` note (R28.07 private release)
        addressed to a *different* room than this turn — or drained during a
        headless turn, which has no room at all — is put back immediately
        rather than rendered: it must never leak that room's private content
        into another room's context.

        Also returns the notes actually consumed this turn (excluding any
        already-requeued misrouted ones) so a turn that fails (or skips)
        before the agent sees them can :meth:`_requeue_notifications`."""
        from contexts.orchestration.infrastructure import pending_notify

        try:
            notes = await pending_notify.drain(agent.id)
        except Exception:
            _log.warning(
                "Redis unavailable for turn context, running without pending context",
                exc_info=True,
            )
            return None, [], []
        if not notes:
            return None, [], []

        misrouted: list[dict[str, Any]] = []
        usable: list[dict[str, Any]] = []
        for n in notes:
            if n.get("kind") == "released_observation" and (
                chatroom_id is None or str(n.get("chatroom_id")) != str(chatroom_id)
            ):
                misrouted.append(n)
            else:
                usable.append(n)
        if misrouted:
            await self._requeue_notifications(agent, misrouted)
        if not usable:
            return None, [], []

        approvals: dict[uuid.UUID, uuid.UUID | None] = {}
        lines: list[str] = []
        for n in usable:
            if n.get("kind") == "approval_request" and n.get("approval_id"):
                try:
                    approval_id = uuid.UUID(str(n["approval_id"]))
                except ValueError:
                    continue
                room_raw = n.get("chatroom_id")
                try:
                    approvals[approval_id] = uuid.UUID(str(room_raw)) if room_raw else None
                except ValueError:
                    approvals[approval_id] = None
                lines.append(
                    f"- Approval requested (approval_id={n['approval_id']}, "
                    f"mode={n.get('mode', '?')}). Call cast_approval_vote to respond."
                )
                if n.get("question"):
                    lines.append(f"  Question: {n['question']}")
            elif n.get("kind") == "released_observation" and n.get("content"):
                # R28.07 — creator-released analysis; readable prose instead of
                # a raw JSON dump so long analyses stay usable in the prompt.
                lines.append(f"- The room owner shared an analysis with you:\n{n['content']}")
            else:
                lines.append(f"- {json.dumps(n, separators=(',', ':'))}")
        tools: list[Tool] = []
        if approvals:
            tools.append(
                build_cast_approval_vote_tool(self._db, agent_id=agent.id, allowed_approvals=approvals)
            )
        return "[Incoming notifications]\n" + "\n".join(lines), tools, usable

    async def _requeue_notifications(self, agent: Agent, notes: list[dict[str, Any]]) -> None:
        """Restore drained-but-unseen notifications (turn failed / skipped
        before the provider call could read them). Best-effort."""
        if not notes:
            return
        try:
            from contexts.orchestration.infrastructure import pending_notify

            await pending_notify.requeue(agent.id, notes)
        except Exception:
            _log.warning(
                "failed to requeue %d pending notifications agent=%s",
                len(notes),
                agent.id,
                exc_info=True,
            )

    async def _run_locked(
        self,
        *,
        agent_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        trigger: str,
        parent_agent_id: uuid.UUID | None,
        input_text: str | None,
        request_id: uuid.UUID | None,
        trigger_message_id: uuid.UUID | None = None,
    ) -> TurnResult:
        agent = await AgentsFacade(self._db).get_agent(agent_id)
        if agent is None:
            # An explicit @mention to a now-deleted agent deserves feedback;
            # autonomous triggers (every_n/silence) stay silent.
            if trigger == "mention":
                await emit_agent_finished_error(chatroom_id, agent_id, "agent_gone")
            return TurnResult(status="skipped", reason="agent_gone")
        # AuthZ tap: re-validate the agent↔room binding at turn start (defends
        # against an unbind racing the trigger). The same query resolves the
        # binding role — observers (R28.01) differ only in output routing.
        role = await ChatroomAgentRepository(self._db).role_of(chatroom_id=chatroom_id, agent_id=agent_id)
        if role is None:
            if trigger == "mention":
                await emit_agent_finished_error(chatroom_id, agent_id, "not_bound")
            return TurnResult(status="skipped", reason="not_bound")
        is_observer = role is ChatroomAgentRole.OBSERVER
        # AuthZ tap: the agent's key group must still belong to the agent's
        # project (defends against a key-group move/delete racing the trigger).
        group = await KeyGroupRepository(self._db).get_active(agent.key_group_id)
        if group is None or group.project_id != agent.project_id:
            await self._audit(
                agent,
                chatroom_id,
                "agent.turn_skipped",
                {"reason": "key_group_scope", "key_group_id": str(agent.key_group_id)},
            )
            await self._db.commit()
            # Actionable for any trigger: a present user otherwise sees the agent
            # fall silent with no hint the key group was moved or deleted.
            # Observer variant goes to the creator channel only (R28.01).
            if is_observer:
                await self._emit_observation_event(
                    chatroom_id, agent.id, "observation.failed", {"kind": "key_group_scope"}
                )
            else:
                await emit_agent_finished_error(chatroom_id, agent.id, "key_group_scope")
            return TurnResult(status="skipped", reason="key_group_scope")
        # Per-(agent, room) turn rate bucket — backstop against trigger storms.
        if not await self._turn_rate_allowed(agent_id, chatroom_id):
            await self._audit(
                agent,
                chatroom_id,
                "agent.turn_skipped",
                {"reason": "rate_limited", "trigger": trigger},
            )
            await self._db.commit()
            # Actionable for any trigger: the rate backstop is informative
            # regardless of who or what triggered the suppressed turn.
            if is_observer:
                await self._emit_observation_event(
                    chatroom_id, agent.id, "observation.failed", {"kind": "rate_limited"}
                )
            else:
                await emit_agent_finished_error(chatroom_id, agent.id, "rate_limited")
            return TurnResult(status="skipped", reason="rate_limited")

        provider = agent.model_hint.value
        models = _resolve_models(agent)
        context_limit = _CONTEXT_LIMITS.get(provider, 128_000)

        # Observer turns get NO room channel at all (R28.01): every emit below
        # is guarded on `room is not None`, and _stream_with_tools already
        # suppresses token deltas for a None room (the headless-turn path). A
        # future emit added without a guard fails closed, not open.
        room = None if is_observer else room_channel(chatroom_id)
        pending_notes: list[dict[str, Any]] = []

        try:
            # Emitted inside the try so any failure still routes to the
            # finished/turn_failed path — the room never stays "thinking".
            await self._audit(agent, chatroom_id, "agent.turn_started", {"trigger": trigger})
            if room is not None:
                await Publisher(room).emit("agent.thinking", {"agent_id": str(agent.id)})
            else:
                await self._emit_observation_event(chatroom_id, agent.id, "observation.started", {})

            # Prompt resolution (R9.04–R9.08).
            base_system, lazy_prompt, section_cache = self._resolve_prompt(agent)

            # Fixed (history-independent) turn context, assembled once. These
            # blocks and the tools are estimated for the compaction decision
            # (F-17) and the knowledge budget (F-16) *before* knowledge is fetched,
            # so knowledge is sized against the space the rest of the turn leaves.
            memory_block = await self._observer_memory_block(agent, chatroom_id) if is_observer else None
            # Drain queued A2A notifications (R9.16); approval requests also add
            # the cast_approval_vote tool for this turn.
            notify_block, extra_tools, pending_notes = await self._pending_context_and_tools(
                agent, chatroom_id
            )
            # code_exec artifacts (charts/files) produced this turn land here and
            # are attached to the reply after it's persisted (Code Interpreter).
            artifact_sink: list[dict[str, Any]] = []
            extra_tools = extra_tools + await self._builtin_tools(
                agent, chatroom_id=chatroom_id, artifact_sink=artifact_sink
            )
            # Resolved once and shared by both consumers below so they see the
            # same snapshot (see _resolve_trigger_attachments docstring).
            trigger_attachments = await self._resolve_trigger_attachments(chatroom_id, trigger_message_id)
            # Stage the triggering message's uploads into the kernel workspace so
            # code_exec can read them; the returned note tells the model the paths.
            staged_note = await self._stage_workspace_inputs(agent, chatroom_id, trigger_attachments)
            # §30 (R30.15): an observer also sees the room's recent structured
            # activity events. Coverage-gated: None when the room has no activities.
            activity_block = await self._activity_context(chatroom_id) if is_observer else None
            registry = build_registry(
                self._db,
                agent_id=agent.id,
                lazy_prompt=lazy_prompt,
                section_cache=section_cache,
                extra=extra_tools,
            )
            tool_specs = registry.specs()
            tool_tokens = tx.estimate_tokens(json.dumps(tool_specs, ensure_ascii=False)) if tool_specs else 0
            input_tokens = tx.estimate_tokens(input_text or "")

            # The turn's system blocks, ordered once with an explicit role each,
            # so the measure and render passes below cannot drift apart.
            system_blocks = _SystemBlocks.build(
                base_system=base_system,
                is_observer=is_observer,
                memory_block=memory_block,
                activity_block=activity_block,
                staged_note=staged_note,
                notify_block=notify_block,
            )

            # F-17: compaction is decided against the whole non-knowledge request
            # (base + dynamic blocks + tools + input + reserve), not history alone.
            prefix_tokens = (
                tx.estimate_tokens(system_blocks.measure([]))
                + tool_tokens
                + input_tokens
                + _DEFAULT_MAX_TOKENS
            )
            history = await self._assemble_history(
                agent, chatroom_id, context_limit, models, extra_projected_tokens=prefix_tokens
            )

            # Request ceiling: the configured cap (or its 75% default) in compact
            # mode, the provider hard limit in general mode — so R11.19 bounds the
            # knowledge blocks in both modes without imposing R9.10 on general.
            if agent.context_mode.value == "compact":
                ceiling = agent.context_token_cap or ctxmod.default_cap_from_limit(context_limit)
            else:
                ceiling = context_limit

            async def _assemble_request(
                history: list[tx.HistoryMessage],
            ) -> tuple[str, list[dict[str, Any]], RagContext | None]:
                summaries = [
                    f"[Earlier conversation summary]\n{hm.content}"
                    for hm in history
                    if hm.role == "system"  # compact_summary
                ]
                # F-16: distribute the knowledge budget by narrow-scope precedence
                # over what remains after the fixed context (system blocks + tools
                # + message history + response reserve). Counting only user/agent
                # rows here avoids double-counting the summaries already in the
                # system-block estimate.
                fixed_context = (
                    tx.estimate_tokens(system_blocks.measure(summaries))
                    + tool_tokens
                    + input_tokens
                    + sum(h.token_count for h in history if h.role in ("user", "agent"))
                )
                total_budget = ctxmod.knowledge_budget(
                    ceiling=ceiling,
                    response_reserve=_DEFAULT_MAX_TOKENS,
                    fixed_context_tokens=fixed_context,
                    safety_margin_frac=_KNOWLEDGE_SAFETY_MARGIN,
                )
                # A zero budget silently drops *every* knowledge block -- the
                # agent then answers from nothing while its config says otherwise,
                # which reads as confabulation rather than as the misconfiguration
                # it is. Fail the turn loudly instead, but only when there was
                # something to drop.
                if await self._knowledge_starved(total_budget, agent, chatroom_id):
                    raise _KnowledgeStarved(fixed_context=fixed_context, ceiling=ceiling)
                budget = ctxmod.KnowledgeBudget(
                    total=total_budget, graph_source_cap=_GRAPH_BLOCK_TOKEN_BUDGET
                )
                # Retrieval keys off the *current* input when this turn carries one
                # (run_input_turn); otherwise the latest user message in history.
                knowledge_queries = _knowledge_queries(history, input_text=input_text)
                knowledge_blocks, rag_ctx = await self._assemble_agent_knowledge(
                    agent, knowledge_queries, chatroom_id=chatroom_id, budget=budget
                )

                # Label history with sender names so the agent can tell participants
                # apart (humans by display name, other agents by their configured
                # name). The running agent's own turns stay unlabelled so the model
                # is not trained to echo a "Name:" prefix on its reply.
                agent_names, user_names = await self._participant_labels(agent, chatroom_id, history)
                # Attachments on the triggering user message become content blocks
                # so the agent can see the file. When this turn carries fresh
                # `input_text`, the trigger is that appended message (below);
                # otherwise it's the message identified by `trigger_message_id` —
                # falling back to the latest user message already in history only
                # when no id was supplied (silence_minutes / coalesced re-enqueue).
                attach_blocks = await self._model_attachment_blocks(chatroom_id, trigger_attachments)
                history_attach_id = None
                if attach_blocks and not input_text:
                    if trigger_message_id is not None:
                        # Only anchor to the exact triggering message. If it fell
                        # out of the loaded history (folded into a compact summary
                        # between enqueue and this turn), there is no historically-
                        # correct row left — splicing onto another message would
                        # misattribute the file (see _provider_message).
                        user_ids_in_history = {h.id for h in history if h.role == "user"}
                        if trigger_message_id in user_ids_in_history:
                            history_attach_id = trigger_message_id
                    else:
                        history_attach_id = next((h.id for h in reversed(history) if h.role == "user"), None)
                request_messages: list[dict[str, Any]] = [
                    self._provider_message(
                        hm,
                        agent.id,
                        agent_names,
                        user_names,
                        attachment_blocks=attach_blocks if hm.id == history_attach_id else None,
                    )
                    for hm in history
                    if hm.role in ("user", "agent")
                ]
                other_agents_present = any(
                    hm.role == "agent"
                    and hm.sender_id not in (None, agent.id)
                    and hm.sender_id in agent_names
                    for hm in history
                )
                # The blocks fold into the system prompt (providers take system as a
                # top-level field, not an in-array role). The participant note is
                # emitted whenever ANY turn will carry a "Name:" prefix:
                # _provider_message prefixes every resolved user/agent label, so
                # gating on >1 user left a single-human room labelled but note-less
                # — the model then treats "Alice:" as literal text.
                assembled_system_text = system_blocks.render(
                    summaries,
                    knowledge_blocks,
                    include_participant_note=other_agents_present or bool(user_names),
                )

                if input_text:
                    if attach_blocks:
                        request_messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": input_text}, *attach_blocks],
                            }
                        )
                    else:
                        request_messages.append({"role": "user", "content": input_text})
                # FIX-02: providers (Anthropic in particular) reject a leading
                # assistant turn. Compaction can fold the range so the first
                # survivor is this agent's own reply — anchor with a neutral turn.
                if request_messages and request_messages[0].get("role") == "assistant":
                    request_messages.insert(0, {"role": "user", "content": _HISTORY_RESUME_NOTE})
                return assembled_system_text, request_messages, rag_ctx

            system_text, messages, rag_ctx = await _assemble_request(history)

            # F-16 AC-6: guard the provider hard limit before the initial dispatch.
            # In compact mode a pathological large prefix runs one more compaction
            # pass rather than dispatching a guaranteed-overflow request; in general
            # mode the provider's own context-limit error surfaces to the UI
            # (R9.09). Mid-tool-loop growth is a separate vector (FU-4).
            if agent.context_mode.value == "compact":
                payload_tokens = (
                    tx.estimate_tokens(system_text)
                    + _estimate_messages_tokens(messages)
                    + tool_tokens
                    + _DEFAULT_MAX_TOKENS
                )
                if payload_tokens > context_limit:
                    _log.warning(
                        "assembled request ~%d tok exceeds provider limit %d; recompacting agent=%s",
                        payload_tokens,
                        context_limit,
                        agent.id,
                    )
                    # Recompact against the NON-history prefix only: _assemble_history
                    # adds the history token_count itself, so passing payload_tokens
                    # (which already counts the history via _estimate_messages_tokens)
                    # would double-count it and over-shed. system_text carries the
                    # knowledge blocks + summaries; input_tokens covers the appended
                    # current turn, which is not part of loaded history.
                    non_history_prefix = (
                        tx.estimate_tokens(system_text) + tool_tokens + input_tokens + _DEFAULT_MAX_TOKENS
                    )
                    history = await self._assemble_history(
                        agent,
                        chatroom_id,
                        context_limit,
                        models,
                        extra_projected_tokens=non_history_prefix,
                    )
                    system_text, messages, rag_ctx = await _assemble_request(history)

            if not messages:
                if room is not None:
                    await Publisher(room).emit("agent.finished", {"agent_id": str(agent.id)})
                elif is_observer:
                    # O-4 (R28.13): a benign skip is not a failure — distinct
                    # event so the creator UI can tell them apart.
                    await self._emit_observation_event(
                        chatroom_id, agent.id, "observation.skipped", {"kind": "no_input"}
                    )
                await self._audit(agent, chatroom_id, "agent.turn_finished", {"empty": True})
                await self._db.commit()
                self._compact_forced_rooms.discard(chatroom_id)
                # The drained notifications were folded into a prompt that will
                # never reach the provider — restore them for the next turn.
                await self._requeue_notifications(agent, pending_notes)
                return TurnResult(status="skipped", reason="no_input")

            # Commit the pre-stream writes (turn_started audit, compaction
            # summary row) so the DB transaction is not held open across the
            # whole provider stream. Mid-stream writes (router usage events,
            # tool audits) and the reply + turn_finished audit form their own
            # transaction committed below — reply persistence stays atomic.
            await self._db.commit()

            final_text, rounds = await self._stream_with_tools(
                agent=agent,
                chatroom_id=chatroom_id,
                parent_agent_id=parent_agent_id,
                system_text=system_text,
                messages=messages,
                models=models,
                registry=registry,
                room=room,
            )

            if not final_text.strip():
                # Nothing to say — never persist an empty agent message.
                await self._audit(
                    agent,
                    chatroom_id,
                    "agent.turn_finished",
                    {"tool_rounds": rounds, "reason": "empty_reply"},
                )
                await self._db.commit()
                self._compact_forced_rooms.discard(chatroom_id)
                if room is not None:
                    await Publisher(room).emit(
                        "agent.finished", {"reason": "empty_reply", "agent_id": str(agent.id)}
                    )
                elif is_observer:
                    # O-4 (R28.13): benign skip, not a failure.
                    await self._emit_observation_event(
                        chatroom_id, agent.id, "observation.skipped", {"kind": "empty_reply"}
                    )
                return TurnResult(status="skipped", reason="empty_reply", tool_rounds=rounds)

            reply_meta: dict[str, Any] = {"trigger": trigger, "tool_rounds": rounds}
            if rag_ctx and rag_ctx.sources:
                # Persist what RAG retrieved so the UI can cite it (R10.09).
                reply_meta["rag_sources"] = rag_ctx.sources

            if is_observer:
                # R28.03: observer output is an observation, never a message —
                # no room emit, no workflow signal, no reply wake-ups.
                obs = await ObservationService(self._db).record(
                    chatroom_id=chatroom_id,
                    agent_id=agent.id,
                    content_md=final_text,
                    trigger=trigger,
                    trigger_message_id=trigger_message_id,
                    metadata=reply_meta,
                )
                await self._audit(
                    agent,
                    chatroom_id,
                    "agent.turn_finished",
                    {"tool_rounds": rounds, "observer": True},
                )
                await self._db.commit()
                self._compact_forced_rooms.discard(chatroom_id)
                # Post-commit, mirroring message.created: the creator's refetch
                # must see the committed row.
                await self._emit_observation_event(
                    chatroom_id,
                    agent.id,
                    "observation.created",
                    {
                        "observation_id": str(obs.id),
                        "created_at": obs.created_at.isoformat() if obs.created_at else None,
                    },
                )
                return TurnResult(status="completed", message_id=None, text=final_text, tool_rounds=rounds)

            msg = await MessageService(self._db).send_agent(
                chatroom_id=chatroom_id,
                agent_id=agent.id,
                content_md=final_text,
                metadata=reply_meta,
                request_id=request_id,
            )
            await self._audit(agent, chatroom_id, "agent.turn_finished", {"tool_rounds": rounds})
            await self._db.commit()
            self._compact_forced_rooms.discard(chatroom_id)
            # Persist any code_exec artifacts (charts/files) and bind them to the
            # reply BEFORE the WS event so the client's refetch hydrates them.
            await self._persist_artifacts(agent, chatroom_id, msg.id, artifact_sink)
            # Publish AFTER commit so a client's refetch sees the committed row
            # (agent replies have no optimistic echo, unlike user sends).
            # `room is not None` always holds here (the observer branch returned
            # above) — the guard exists to narrow the type and stay fail-closed.
            if room is not None:
                pub = Publisher(room)
                await pub.emit(
                    "message.created",
                    {
                        "message_id": str(msg.id),
                        "sender_type": "agent",
                        "sender_id": str(agent.id),
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    },
                )
                await pub.emit("agent.finished", {"message_id": str(msg.id), "agent_id": str(agent.id)})
            # K.4: agent replies feed workflow `message` triggers/waits exactly
            # like user sends do (sender_filter agent/any). Best-effort,
            # post-commit — never fails the turn.
            await self._dispatch_agent_message_signal(chatroom_id, final_text)
            # R15.01: an agent reply counts toward other bound agents'
            # every_n triggers and touches their silence timers; R11.02:
            # it also feeds GraphRAG message triggers.
            await self._dispatch_agent_reply_wakeups(agent, chatroom_id, msg.id)
            return TurnResult(status="completed", message_id=msg.id, text=final_text, tool_rounds=rounds)

        except _KnowledgeStarved as ks:
            # The fixed context left nothing for the knowledge blocks, so every
            # bound source would have been dropped in silence. Skipping loudly is
            # the point: the agent would otherwise answer as if it had consulted
            # its sources. Actionable for any trigger, so no trigger check here.
            #
            # `fixed_context` is audited because the cap is not always the cause:
            # it also carries the turn's input and history, so one very long
            # message can floor the budget on a perfectly reasonable cap. Without
            # both numbers the operator cannot tell those two cases apart.
            _log.warning(
                "knowledge budget floored agent=%s room=%s fixed_context=%d ceiling=%d cap=%s",
                agent_id,
                chatroom_id,
                ks.fixed_context,
                ks.ceiling,
                agent.context_token_cap,
            )
            await self._db.rollback()
            try:
                await self._audit(
                    agent,
                    chatroom_id,
                    "agent.turn_skipped",
                    {
                        "reason": "knowledge_starved",
                        "context_mode": agent.context_mode.value,
                        "context_token_cap": agent.context_token_cap,
                        "fixed_context_tokens": ks.fixed_context,
                        "ceiling_tokens": ks.ceiling,
                    },
                )
                await self._db.commit()
            except Exception:
                _log.exception("agent turn knowledge-starved bookkeeping failed")
            try:
                if is_observer:
                    await self._emit_observation_event(
                        chatroom_id, agent.id, "observation.failed", {"kind": "knowledge_starved"}
                    )
                else:
                    await emit_agent_finished_error(chatroom_id, agent.id, "knowledge_starved")
            except Exception:
                _log.exception("agent turn knowledge-starved WS emit failed")
            # The agent never acted on the drained notifications — restore them.
            await self._requeue_notifications(agent, pending_notes)
            # Re-arm the one-shot /compact flag this turn consumed but wasted.
            await self._restore_compact_flag(chatroom_id)
            return TurnResult(status="skipped", reason="knowledge_starved")

        except Exception as exc:
            _log.exception("agent turn failed agent=%s room=%s", agent_id, chatroom_id)
            await self._db.rollback()
            # Never leave the room stuck in "thinking". The WS emit and the
            # audit row are independently guarded: a Redis outage must not
            # swallow the agent.turn_failed audit (DB), and vice versa.
            try:
                if room is not None:
                    await Publisher(room).emit(
                        "agent.finished", {"error": _err_kind(exc), "agent_id": str(agent.id)}
                    )
                else:
                    await self._emit_observation_event(
                        chatroom_id, agent.id, "observation.failed", {"kind": _err_kind(exc)}
                    )
            except Exception:
                _log.exception("agent turn failure-path WS emit failed")
            try:
                await self._audit(agent, chatroom_id, "agent.turn_failed", {"error": _err_kind(exc)})
                await self._db.commit()
            except Exception:
                _log.exception("agent turn failure-path bookkeeping failed")
            # The agent never acted on the drained notifications — restore them.
            await self._requeue_notifications(agent, pending_notes)
            # Re-arm the one-shot /compact flag this turn consumed but wasted.
            await self._restore_compact_flag(chatroom_id)
            return TurnResult(status="failed", reason=_err_kind(exc))

    async def _observer_memory_block(self, agent: Agent, chatroom_id: uuid.UUID) -> str | None:
        """R28.05 — the observer's own recent observations, oldest-first.
        Best-effort: a DB hiccup costs the memory block, not the turn. The
        query runs under a SAVEPOINT so a failure rolls back only this
        lookup — a plain ``self._db.rollback()`` would discard the whole
        transaction, including the turn's already-pending
        ``agent.turn_started`` audit insert."""
        try:
            async with self._db.begin_nested():
                rows = await ObservationRepository(self._db).list_recent_for_agent(
                    chatroom_id=chatroom_id, agent_id=agent.id, limit=OBSERVER_MEMORY_WINDOW
                )
        except Exception:
            _log.warning("observer memory fetch failed for agent %s", agent.id, exc_info=True)
            return None
        if not rows:
            return None
        lines = [f"- ({o.created_at.isoformat() if o.created_at else '?'}) {o.content_md}" for o in rows]
        return "[Your previous observations]\n" + "\n".join(lines)

    async def _emit_observation_event(
        self,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit an ``observation.*`` event on the creator's user channel
        (R28.13). Ids only — bodies are fetched over REST. Legacy NULL-creator
        rooms have no push recipient; moderators read over REST instead.
        Best-effort: a Redis hiccup never fails the turn. The recipient
        lookup runs under a SAVEPOINT — same rationale as
        :meth:`_observer_memory_block` — so a DB hiccup here can't discard
        the turn's already-pending ``agent.turn_started`` audit insert."""
        try:
            async with self._db.begin_nested():
                recipient = await ObservationService(self._db).recipient_user_id(chatroom_id)
        except Exception:
            _log.warning("observation event emit failed for room %s", chatroom_id, exc_info=True)
            return
        if recipient is None:
            return
        try:
            await Publisher(user_channel(recipient)).emit(
                event,
                {"chatroom_id": str(chatroom_id), "agent_id": str(agent_id), **payload},
            )
        except Exception:
            _log.warning("observation event emit failed for room %s", chatroom_id, exc_info=True)

    async def _dispatch_agent_message_signal(self, chatroom_id: uuid.UUID, content: str) -> None:
        """Mirror of the user-send route's workflow signal dispatch
        (``app.api.v1.messages._dispatch_message_workflow_signal``) with
        ``sender_type="agent"``. Best-effort and post-commit."""
        try:
            from shared_kernel.queue import enqueue

            await enqueue(
                "workflow_signal",
                "message",
                {
                    "chatroom_id": str(chatroom_id),
                    "sender_type": "agent",
                    "content": content,
                },
            )
        except Exception:
            _log.warning(
                "workflow message-signal dispatch failed for room %s",
                chatroom_id,
                exc_info=True,
            )

    async def _dispatch_agent_reply_wakeups(
        self, agent: Agent, chatroom_id: uuid.UUID, message_id: uuid.UUID
    ) -> None:
        """R15.01: an agent reply counts toward other bound agents' every_n
        triggers and touches their silence timers; R11.02: it also feeds
        GraphRAG message triggers. Best-effort and post-commit — a failure
        here must never fail the turn."""
        try:
            from contexts.conversation.application.triggers import (
                evaluate_message_wakeups,
                list_bound_agents,
            )
            from contexts.knowledge.interfaces.facade import KnowledgeFacade
            from shared_kernel.queue import enqueue

            bound = await list_bound_agents(self._db, chatroom_id)
            fired = await evaluate_message_wakeups(
                self._db,
                chatroom_id=chatroom_id,
                sender_is_user=False,
                sender_agent_id=agent.id,
                bound_agents=bound,
            )
            for aid in fired:
                await enqueue(
                    "wakeup_agent",
                    str(aid),
                    str(chatroom_id),
                    "every_n_messages",
                    str(message_id),
                )
            if bound:
                triggers = await KnowledgeFacade(self._db).evaluate_graphrag_message_triggers(
                    chatroom_id=chatroom_id, agent_ids=[a.agent_id for a in bound]
                )
                for trig in triggers:
                    # D5: dedup concurrent triggers for the same config+watermark
                    # onto one queued build via a stable job id.
                    await enqueue(
                        "graphrag_build",
                        config_id=str(trig.config_id),
                        triggered_by=trig.triggered_by,
                        _job_id=trig.job_id,
                    )
        except Exception:
            _log.warning(
                "agent-reply wakeup dispatch failed room=%s",
                chatroom_id,
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                await self._db.rollback()

    async def _turn_rate_allowed(self, agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> bool:
        """Sliding-window per-(agent, room) turn cap. Fails open: a rate-limit
        infrastructure fault must not silence the agent."""
        try:
            from shared_kernel.auth import ratelimit

            decision = await ratelimit.check_raw(
                key=f"rl:agent-turn:{agent_id}:{chatroom_id}",
                window_sec=_TURN_RATE_WINDOW_S,
                max_count=_TURN_RATE_MAX_TURNS,
            )
            return decision.allowed
        except Exception:
            _log.warning(
                "turn rate-limit check failed agent=%s room=%s (allowing)",
                agent_id,
                chatroom_id,
                exc_info=True,
            )
            return True

    # ----------------------------------------------------------------- #

    async def _participant_labels(
        self,
        agent: Agent,
        chatroom_id: uuid.UUID,
        history: list[tx.HistoryMessage],
    ) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
        """Resolve ``(agent_id -> name, user_id -> label)`` for labelling.

        Human authors resolve in precedence order: room guest label, then account
        display name, then the login email (the model context deliberately falls
        back to email so an agent can always tell speakers apart -- see
        ``IdentityFacade.get_chat_labels``), then a generic ``Guest``. Agents
        resolve to their configured name.
        """
        agent_ids = {hm.sender_id for hm in history if hm.role == "agent" and hm.sender_id is not None}
        agent_names = await AgentRepository(self._db).names_for_ids(list(agent_ids))
        guest_names = {
            g.user_id: g.display_name for g in await ConversationFacade(self._db).list_guests(chatroom_id)
        }
        user_ids = {hm.sender_id for hm in history if hm.role == "user" and hm.sender_id is not None}
        account_labels = await IdentityFacade(self._db).get_chat_labels(list(user_ids))
        user_names = {uid: (guest_names.get(uid) or account_labels.get(uid) or "Guest") for uid in user_ids}
        return agent_names, user_names

    @staticmethod
    def _provider_message(
        hm: tx.HistoryMessage,
        running_agent_id: uuid.UUID,
        agent_names: dict[uuid.UUID, str],
        user_names: dict[uuid.UUID, str],
        attachment_blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Map one history row to a provider message, prefixing the sender name.

        The running agent's own turns are left unprefixed (and stay ``assistant``)
        so the model does not learn to echo a ``Name:`` prefix in its reply.

        When ``attachment_blocks`` is supplied (only for the triggering user
        message), the message carries multi-part content — the prefixed text
        followed by the neutral attachment blocks — so the adapters can render
        the file for the model to see.

        An older user message's ``attachment_excerpt`` (bounded extracted text
        from a file that is no longer the triggering message) is folded into
        the plain text instead — but only when this row does NOT already carry
        live ``attachment_blocks``, so a file's content is never shown twice
        (once as rich vision/PDF blocks, once as a plain-text excerpt).
        """
        if hm.role == "agent":
            if hm.sender_id == running_agent_id:
                return {"role": "assistant", "content": hm.content}
            label = agent_names.get(hm.sender_id) if hm.sender_id is not None else None
            content = f"{label}: {hm.content}" if label else hm.content
            # Other agents are external actors from this agent's perspective;
            # mapping them to "user" prevents consecutive assistant turns which
            # the Anthropic and OpenAI APIs reject with a 400.
            return {"role": "user", "content": content}
        label = user_names.get(hm.sender_id) if hm.sender_id is not None else None
        content = f"{label}: {hm.content}" if label else hm.content
        if hm.attachment_excerpt and not attachment_blocks:
            content = f"{content}\n\n{hm.attachment_excerpt}" if content else hm.attachment_excerpt
        if attachment_blocks:
            text_blocks = [{"type": "text", "text": content}] if content else []
            return {"role": "user", "content": text_blocks + attachment_blocks}
        return {"role": "user", "content": content}

    async def _model_attachment_blocks(
        self, chatroom_id: uuid.UUID, attachments: list[MessageAttachment]
    ) -> list[dict[str, Any]]:
        """Neutral content blocks for the triggering message's attachments, so
        the agent can see uploaded images/PDFs/text. Best-effort — a storage or
        decode fault must never abort the turn."""
        try:
            if not attachments:
                return []
            facade = ConversationFacade(self._db)
            attachments = list(attachments[: mattach.MAX_ATTACH_FILES])
            blobs = await facade.read_attachments_bytes(attachments)
            items = [(att.filename, att.mime, data) for att, data in zip(attachments, blobs, strict=True)]
            return mattach.build_blocks(items)
        except Exception:
            _log.warning("model attachment assembly failed for room %s", chatroom_id, exc_info=True)
            return []

    async def _assemble_history(
        self,
        agent: Agent,
        chatroom_id: uuid.UUID,
        context_limit: int,
        models: dict[str, str],
        *,
        extra_projected_tokens: int = 0,
    ) -> list[tx.HistoryMessage]:
        """Load model-facing history, compacting it when the *next request* would
        cross the cap (R9.10).

        ``extra_projected_tokens`` is the estimated non-knowledge prefix of the
        assembled request — base + dynamic system blocks + tools + response
        reserve (F-17). The compact-mode decision is made against
        ``history + extra_projected_tokens``, not history alone, so a large
        prompt/tool prefix triggers compaction before the provider limit is hit.
        The forced ``/compact`` half-shed (G.10) stays history-based — it is the
        user's explicit "shed half of history now" action, independent of the
        next request's size.
        """
        from shared_kernel.realtime.distributed_lock import distributed_lock

        history = await tx.load_model_history(self._db, chatroom_id=chatroom_id)
        projected = sum(h.token_count for h in history)
        # A POST /compact sets a one-shot flag (G.10); honour it regardless of
        # mode/cap by capping at half the current projection so a range is shed.
        forced = await self._consume_compact_flag(chatroom_id)
        if forced:
            cap: int | None = max(1, projected // 2)
            compact_projected = projected
        elif agent.context_mode.value == "compact" and ctxmod.should_compact(
            mode="compact",
            projected_tokens=projected + extra_projected_tokens,
            context_token_cap=agent.context_token_cap,
            provider_context_limit=context_limit,
        ):
            cap = agent.context_token_cap
            compact_projected = projected + extra_projected_tokens
        else:
            return history
        # FIX-11: room-scoped lock prevents duplicate summaries when two agents'
        # turns in the same room both cross the cap concurrently.
        async with distributed_lock(f"compact:lock:{chatroom_id}", ttl_s=300) as acquired:
            if not acquired:
                if forced:
                    await self._restore_compact_flag(chatroom_id)
                return history
            # Re-check staleness: another turn may have compacted while we waited.
            history = await tx.load_model_history(self._db, chatroom_id=chatroom_id)
            projected = sum(h.token_count for h in history)
            if forced:
                cap = max(1, projected // 2)
                compact_projected = projected
            elif not ctxmod.should_compact(
                mode="compact",
                projected_tokens=projected + extra_projected_tokens,
                context_token_cap=agent.context_token_cap,
                provider_context_limit=context_limit,
            ):
                return history
            else:
                compact_projected = projected + extra_projected_tokens
            summariser = RouterSummariser(
                router=self._router,
                key_group_id=agent.key_group_id,
                models=models,
                agent_id=agent.id,
            )
            store = tx.MessagesTranscriptStore(self._db, chatroom_id=chatroom_id)
            try:
                did = await ctxmod.run_compact(
                    messages=cast("list[ctxmod.MessageLike]", history),
                    projected_tokens=compact_projected,
                    context_token_cap=cap,
                    provider_context_limit=context_limit,
                    summariser=summariser,
                    store=store,
                )
            except ctxmod.CompactFailed as exc:
                _log.warning("compaction failed agent=%s: %s", agent.id, exc)
                await self._audit(agent, chatroom_id, "agent.compact_failed", {"error": str(exc)})
                return history
            if not did:
                if forced:
                    await self._restore_compact_flag(chatroom_id)
                return history
        # Reload so the summary replaces the folded range.
        reloaded = await tx.load_model_history(self._db, chatroom_id=chatroom_id)
        await self._audit(
            agent,
            chatroom_id,
            "agent.compact_run",
            {
                "forced": forced,
                "token_cap": cap,
                "tokens_before": projected,
                "tokens_after": sum(h.token_count for h in reloaded),
                "messages_before": len(history),
                "messages_after": len(reloaded),
            },
        )
        return reloaded

    async def run_compaction(self, *, agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> bool:
        """Headless compaction pass (G.10) — runs the turn engine's compaction
        machinery without a provider turn. Used by the ``compact_chatroom``
        worker task enqueued by POST /compact; the one-shot Redis flag set by
        the endpoint forces the pass inside :meth:`_assemble_history`."""
        agent = await AgentsFacade(self._db).get_agent(agent_id)
        if agent is None:
            return False
        provider = agent.model_hint.value
        context_limit = _CONTEXT_LIMITS.get(provider, 128_000)
        try:
            await self._assemble_history(agent, chatroom_id, context_limit, _resolve_models(agent))
            await self._db.commit()
            self._compact_forced_rooms.discard(chatroom_id)
            return True
        except Exception:
            _log.exception("headless compaction failed agent=%s room=%s", agent_id, chatroom_id)
            await self._db.rollback()
            await self._restore_compact_flag(chatroom_id)
            return False

    async def _consume_compact_flag(self, chatroom_id: uuid.UUID) -> bool:
        """Atomically read-and-clear (GETDEL) the forced-compaction flag set by
        POST /compact. Consumed rooms are tracked so a failed turn can re-arm
        the flag via :meth:`_restore_compact_flag`."""
        try:
            from shared_kernel.auth.clients import get_redis

            val = await get_redis().getdel(f"compact:pending:{chatroom_id}")
            if val:
                self._compact_forced_rooms.add(chatroom_id)
                return True
            return False
        except Exception:
            _log.warning("Failed to read compact flag", exc_info=True)
            return False

    async def _restore_compact_flag(self, chatroom_id: uuid.UUID) -> None:
        """Re-arm the one-shot /compact flag if this engine consumed it but the
        consuming turn failed before committing a compaction. Best-effort."""
        if chatroom_id not in self._compact_forced_rooms:
            return
        try:
            from shared_kernel.auth.clients import get_redis

            await get_redis().set(f"compact:pending:{chatroom_id}", "1", ex=3600)
            self._compact_forced_rooms.discard(chatroom_id)
        except Exception:
            _log.warning("failed to restore compact flag for room %s", chatroom_id, exc_info=True)

    async def _stream_with_tools(
        self,
        *,
        agent: Agent,
        chatroom_id: uuid.UUID | None,
        parent_agent_id: uuid.UUID | None,
        system_text: str,
        messages: list[dict[str, Any]],
        models: dict[str, str],
        registry: Any,
        room: str | None,
        cancel_check: CancelCheck | None = None,
    ) -> tuple[str, int]:
        tool_specs = registry.specs()
        last_text = ""
        for rounds in range(1, MAX_TOOL_ROUNDS + 1):
            if cancel_check is not None and await cancel_check():
                raise _TurnCancelled(rounds - 1)
            payload: dict[str, Any] = {
                "models": models,
                "system": system_text,
                "messages": messages,
                "max_tokens": _DEFAULT_MAX_TOKENS,
            }
            if tool_specs:
                payload["tools"] = tool_specs
            if agent.effort:
                payload["effort"] = agent.effort.value
            payload.update(_sampling_payload(agent))
            request = ProviderRequest(
                capability=ProviderCapability.LLM_CHAT,
                payload=payload,
                agent_id=agent.id,
                parent_agent_id=parent_agent_id,
                chatroom_id=chatroom_id,
            )
            body: dict[str, Any] = {}
            async for ev in self._router.call_stream(group_id=agent.key_group_id, request=request):
                if isinstance(ev, TokenDelta):
                    AGENT_STREAM_TOKENS_TOTAL.inc()
                    if room is not None:
                        await Publisher(room).emit(
                            "agent.token", {"text": ev.text, "agent_id": str(agent.id)}
                        )
                elif isinstance(ev, StreamComplete):
                    body = ev.result.body

            last_text = str(body.get("text", ""))
            tool_calls = body.get("tool_calls") or []
            if not tool_calls:
                return last_text, rounds - 1
            # Append the assistant tool-use turn, then each tool result.
            messages.append({"role": "assistant", "content": last_text, "tool_calls": tool_calls})
            for tc in tool_calls:
                result = await registry.call(tc.get("name"), tc.get("arguments") or {})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": tc.get("name"),
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )
        # Tool-round budget exhausted — give the model one final turn WITHOUT
        # tools so it can formulate a coherent reply from the accumulated tool
        # results instead of returning the partial text from the last tool-use
        # response (which is typically "let me check…" or empty).
        if cancel_check is not None and await cancel_check():
            raise _TurnCancelled(MAX_TOOL_ROUNDS)
        #
        # Strip tool_calls / role:tool from the history so the provider API
        # doesn't require a `tools` field (Anthropic rejects tool_use/tool_result
        # content blocks when `tools` is absent).
        final_messages: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "")
            if role == "tool":
                final_messages.append(
                    {
                        "role": "user",
                        "content": f"[Tool result: {m.get('name', 'unknown')}]\n{m.get('content', '')}",
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                text = m.get("content") or ""
                names = [tc.get("name", "?") for tc in m["tool_calls"]]
                final_messages.append(
                    {
                        "role": "assistant",
                        "content": f"{text}\n[Used tools: {', '.join(names)}]".strip(),
                    }
                )
            else:
                final_messages.append(m)
        final_payload: dict[str, Any] = {
            "models": models,
            "system": system_text,
            "messages": final_messages,
            "max_tokens": _DEFAULT_MAX_TOKENS,
        }
        if agent.effort:
            final_payload["effort"] = agent.effort.value
        final_payload.update(_sampling_payload(agent))
        final_request = ProviderRequest(
            capability=ProviderCapability.LLM_CHAT,
            payload=final_payload,
            agent_id=agent.id,
            parent_agent_id=parent_agent_id,
            chatroom_id=chatroom_id,
        )
        try:
            final_body: dict[str, Any] = {}
            async for ev in self._router.call_stream(group_id=agent.key_group_id, request=final_request):
                if isinstance(ev, TokenDelta):
                    AGENT_STREAM_TOKENS_TOTAL.inc()
                    if room is not None:
                        await Publisher(room).emit(
                            "agent.token", {"text": ev.text, "agent_id": str(agent.id)}
                        )
                elif isinstance(ev, StreamComplete):
                    final_body = ev.result.body
            return str(final_body.get("text", last_text)), MAX_TOOL_ROUNDS
        except Exception:
            _log.warning("final no-tools call failed; falling back to last tool-round text")
            return last_text, MAX_TOOL_ROUNDS

    async def _rag_context(
        self, agent: Agent, queries: Sequence[str], *, token_budget: int | None = None
    ) -> RagContext | None:
        """Delegate to the knowledge-context :class:`RagContextProvider`."""
        return await self._rag_provider.query(
            rag_config_id=agent.rag_config_id,
            query_texts=queries,
            agent_id=agent.id,
            token_budget=token_budget,
        )

    async def _graphrag_context(
        self,
        agent: Agent,
        chatroom_id: uuid.UUID,
        queries: Sequence[str],
        *,
        token_budget: int | None = None,
    ) -> str | None:
        """Delegate to the knowledge-context :class:`GraphRagContextProvider`.

        Resolves every Concept Map covering the agent in the current room (WS4):
        the chatroom map, each enabled agent_group map the agent is a live member
        of, and the enabled workspace map — ordered narrow -> wide — then runs the
        tiered assembler. An empty result means no covering map; a single
        chatroom layer reproduces the pre-2b flat output (AC-5).
        """
        from contexts.knowledge.interfaces.facade import KnowledgeFacade

        layers = await KnowledgeFacade(self._db).resolve_graphrag_layers(
            agent_id=agent.id, chatroom_id=chatroom_id
        )
        if not layers:
            return None
        return await self._graphrag_provider.query_layers(
            graphrag_config_ids=[cfg.id for cfg in layers],
            query_texts=queries,
            querying_agent_id=agent.id,
            token_budget=token_budget,
        )

    async def _knowmap_context(
        self, agent: Agent, queries: Sequence[str], *, token_budget: int | None = None
    ) -> str | None:
        """Delegate to the knowledge-context :class:`KnowledgeMapContextProvider`.

        Keyed on the agent's own ``knowmap_config_id`` (per-Agent binding, R11.14) —
        independent of any Concept Map layer resolution. The provider enforces the
        per-Agent document allowlist edge filter and degrades to ``None`` on failure.
        """
        return await self._knowmap_provider.query(
            knowmap_config_id=agent.knowmap_config_id,
            query_texts=queries,
            querying_agent_id=agent.id,
            token_budget=token_budget,
        )

    async def _knowledge_starved(
        self, total_budget: int, agent: Agent, chatroom_id: uuid.UUID | None
    ) -> bool:
        """True when a floored knowledge budget would drop sources the agent has
        actually bound (R11.19 / F-16).

        ``knowledge_budget`` floors at 0 whenever the fixed context alone fills the
        ceiling, and every downstream ``if remaining > 0`` guard then skips — so
        File RAG, the Concept Map and the Knowledge Map vanish together. An agent
        with no bound source loses nothing and must not be disturbed; one with a
        bound source has silently lost all of it.

        Split out of the assembly closure so the decision is reachable from a unit
        test — the closure is not.
        """
        if total_budget > 0:
            return False
        return await self._has_knowledge_source(agent, chatroom_id)

    async def _has_knowledge_source(self, agent: Agent, chatroom_id: uuid.UUID | None) -> bool:
        """True when this turn had knowledge available to inject.

        Separates "the budget dropped everything" from "there was nothing to
        drop", so the starvation guard fires only on real loss. The two per-Agent
        bindings are free to check and short-circuit the room-scoped Concept Map
        lookup. Best-effort: a failed lookup reports no source rather than
        converting a working turn into a failed one — the query runs under a
        SAVEPOINT so a failure rolls back only this lookup, same rationale as
        :meth:`_observer_memory_block`. Without it the aborted transaction would
        fail every later statement in the turn, which is the outcome this guard
        exists to avoid.
        """
        if agent.rag_config_id is not None or agent.knowmap_config_id is not None:
            return True
        if chatroom_id is None:
            return False
        try:
            from contexts.knowledge.interfaces.facade import KnowledgeFacade

            async with self._db.begin_nested():
                layers = await KnowledgeFacade(self._db).resolve_graphrag_layers(
                    agent_id=agent.id, chatroom_id=chatroom_id
                )
        except Exception:
            _log.warning("concept-map layer lookup failed for agent %s", agent.id, exc_info=True)
            return False
        return bool(layers)

    async def _assemble_agent_knowledge(
        self,
        agent: Agent,
        queries: Sequence[str],
        *,
        chatroom_id: uuid.UUID | None,
        budget: ctxmod.KnowledgeBudget | None = None,
    ) -> tuple[list[str], RagContext | None]:
        """Assemble the per-turn knowledge system blocks in narrow-scope order.

        Shared by the room path (:meth:`_run_locked`) and the headless path
        (:meth:`run_input_turn`) so the two cannot drift (R10.09/R11.14). File RAG
        and the attached Knowledge Map are per-Agent bindings needing no room;
        Concept Maps are room-scoped and included only when a real ``chatroom_id``
        is supplied. Blocks are placed in narrow-scope order (File RAG → Concept
        Map → Knowledge Map), mirroring the room path's former inline assembly.

        When ``budget`` is supplied (F-16/R11.19) the two graph blocks draw first
        in precedence order (Concept Map, then Knowledge Map), each capped at
        ``budget.graph_source_cap``; File RAG then receives the *measured*
        remainder of ``budget.total`` — so a graph source that resolves to nothing
        returns its reservation to File RAG rather than stranding it. A source
        whose remaining budget is zero is omitted (never queried, never sent
        empty). ``budget=None`` leaves every block uncapped — the headless path,
        pending FU.

        Returns the blocks in placement order plus the ``RagContext`` so the room
        path can persist RAG citations (``reply_meta['rag_sources']``); headless
        callers ignore the second element.
        """
        concept_block: str | None = None
        knowmap_block: str | None = None
        rag_ctx: RagContext | None = None

        if budget is None:
            # Uncapped (headless): query every bound source with no trimming.
            if chatroom_id is not None:
                concept_block = await self._graphrag_context(agent, chatroom_id, queries)
            knowmap_block = await self._knowmap_context(agent, queries)
            rag_ctx = await self._rag_context(agent, queries)
        else:
            # Concept Map (highest precedence) then Knowledge Map draw first, each
            # up to the graph cap; File RAG takes what they leave, measured from
            # what they actually render, so an absent graph source returns its
            # reservation instead of stranding it. A source left zero is skipped.
            remaining = budget.total
            cap = budget.graph_source_cap
            if chatroom_id is not None and remaining > 0:
                concept_block = await self._graphrag_context(
                    agent, chatroom_id, queries, token_budget=min(cap, remaining)
                )
                if concept_block:
                    remaining = max(0, remaining - tx.estimate_tokens(concept_block))
            if remaining > 0:
                knowmap_block = await self._knowmap_context(agent, queries, token_budget=min(cap, remaining))
                if knowmap_block:
                    remaining = max(0, remaining - tx.estimate_tokens(knowmap_block))
            if remaining > 0:
                rag_ctx = await self._rag_context(agent, queries, token_budget=remaining)

        blocks: list[str] = []
        if rag_ctx:
            blocks.append(rag_ctx.block)
        if concept_block:
            blocks.append(concept_block)
        if knowmap_block:
            blocks.append(knowmap_block)
        return blocks, rag_ctx

    async def _activity_context(self, chatroom_id: uuid.UUID) -> str | None:
        """Delegate to the activities :class:`ActivityContextProvider` (R30.15).

        Coverage-gated inside the provider (returns ``None`` when the room has no
        activity events) and best-effort (``None`` on any failure), so a broken
        activities read never breaks the observer turn."""
        return await self._activity_provider.query(chatroom_id=chatroom_id)

    async def _audit(
        self,
        agent: Agent,
        chatroom_id: uuid.UUID | None,
        action: str,
        extra: dict[str, Any],
    ) -> None:
        meta: dict[str, Any] = dict(extra)
        if chatroom_id is not None:
            meta["chatroom_id"] = str(chatroom_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                resource_type="agent",
                resource_id=agent.id,
                metadata=meta,
            ),
        )


def _err_kind(exc: Exception) -> str:
    if isinstance(exc, KeyGroupExhausted):
        return f"provider_exhausted:{exc.reason}"
    if isinstance(exc, ProviderStreamError):
        return "provider_stream_failed"
    return exc.__class__.__name__


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Coarse token estimate of a provider ``messages`` list (F-16 pre-dispatch
    guard). Content is either a plain string or a list of content blocks."""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += tx.estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        total += tx.estimate_tokens(text)
    return total


def _knowledge_queries(history: Sequence[tx.HistoryMessage], *, input_text: str | None) -> list[str]:
    """Build compact retrieval queries from the current turn plus nearby context."""
    current = (input_text or "").strip()
    if not current:
        current = next(
            (h.content.strip() for h in reversed(history) if h.role == "user" and h.content.strip()),
            "",
        )
    if not current:
        return []

    queries: list[str] = []

    def add(raw: str) -> None:
        text = " ".join(raw.split())
        if not text:
            return
        clipped = text[:_MAX_KNOWLEDGE_QUERY_CHARS]
        if clipped not in queries:
            queries.append(clipped)

    add(current)

    recent: list[str] = []
    skipped_current = input_text is not None
    for msg in reversed(history):
        if msg.role not in {"user", "agent"} or not msg.content.strip():
            continue
        if not skipped_current and msg.role == "user" and msg.content.strip() == current:
            skipped_current = True
            continue
        label = "User" if msg.role == "user" else "Assistant"
        recent.append(f"{label}: {msg.content.strip()}")
        if len(recent) >= 4:
            break
    if recent:
        add("Recent conversation:\n" + "\n".join(reversed(recent)) + f"\nCurrent question:\n{current}")

    summary = next(
        (h.content.strip() for h in reversed(history) if h.role == "system" and h.content.strip()),
        "",
    )
    if summary:
        add(f"Earlier conversation summary:\n{summary}\nCurrent question:\n{current}")

    return queries[:_MAX_KNOWLEDGE_QUERIES]


__all__ = ["CancelCheck", "MAX_TOOL_ROUNDS", "TurnEngine", "TurnResult"]
