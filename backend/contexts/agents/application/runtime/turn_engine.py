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
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from prometheus_client import Counter, Histogram
from sqlalchemy.ext.asyncio import AsyncSession

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
from contexts.agents.infrastructure.turn_lock import DEFAULT_TURN_TTL_S, turn_lock
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.application.message_service import MessageService
from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository
from contexts.conversation.interfaces import emit_agent_finished_error, room_channel
from contexts.conversation.interfaces.facade import ConversationFacade
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
from contexts.knowledge.application.graphrag_context_provider import GraphRagContextProvider
from contexts.knowledge.application.rag_context_provider import RagContext, RagContextProvider
from shared_kernel import audit
from shared_kernel.observability.metrics import REGISTRY
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
_DEFAULT_MAX_TOKENS = 4096

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


def _queued_trigger_key(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"turn:queued:{agent_id}:{chatroom_id}"


async def _mark_trigger_queued(agent_id: uuid.UUID, chatroom_id: uuid.UUID, trigger: str) -> None:
    """Record (at most once — SETNX) that a trigger landed while a turn held
    the lock, so the lock holder re-enqueues exactly one follow-up turn."""
    try:
        from shared_kernel.auth.clients import get_redis

        await get_redis().set(
            _queued_trigger_key(agent_id, chatroom_id),
            trigger,
            nx=True,
            ex=DEFAULT_TURN_TTL_S,
        )
    except Exception:
        _log.warning(
            "failed to queue coalesced trigger agent=%s room=%s",
            agent_id,
            chatroom_id,
            exc_info=True,
        )


async def _pop_queued_trigger(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str | None:
    """Atomically read-and-clear the coalesced-trigger flag (GETDEL)."""
    try:
        from shared_kernel.auth.clients import get_redis

        val = await get_redis().getdel(_queued_trigger_key(agent_id, chatroom_id))
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
    return val.decode() if isinstance(val, bytes) else str(val)


@dataclass(frozen=True, slots=True)
class TurnResult:
    status: str  # "completed" | "skipped" | "failed"
    reason: str | None = None
    message_id: uuid.UUID | None = None
    text: str = ""
    tool_rounds: int = 0


class TurnEngine:
    def __init__(
        self,
        db: AsyncSession,
        *,
        router: ProviderRouter | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
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
        )
        self._graphrag_provider = GraphRagContextProvider(
            db,
            router=self._router,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
        )
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
    ) -> TurnResult:
        started = time.monotonic()
        async with turn_lock(agent_id, chatroom_id) as acquired:
            if not acquired:
                # A turn is already running for this (agent, room). Park the
                # trigger (SETNX = at most one) so the lock holder re-enqueues
                # a follow-up turn after release — without this, a user message
                # landing mid-turn would never get a reply.
                await _mark_trigger_queued(agent_id, chatroom_id, trigger)
                AGENT_TURNS_TOTAL.labels(result="skipped").inc()
                return TurnResult(status="skipped", reason="locked")
            result = await self._run_locked(
                agent_id=agent_id,
                chatroom_id=chatroom_id,
                trigger=trigger,
                parent_agent_id=parent_agent_id,
                input_text=input_text,
                request_id=request_id,
            )
        # Lock released — drain the coalesced trigger (if any) into exactly one
        # follow-up wakeup so the message that arrived mid-turn gets a reply.
        queued = await _pop_queued_trigger(agent_id, chatroom_id)
        if queued is not None:
            try:
                from shared_kernel.queue import enqueue

                await enqueue("wakeup_agent", str(agent_id), str(chatroom_id), queued)
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
    ) -> TurnResult:
        """Headless input→reply turn for A2A ``call`` / ``instruct`` (K.3 Pass 2).

        No chatroom: no room history, no reply persistence, no room binding
        check (the A2A scope check already authorised the caller), and no WS
        stream (there is no room subscriber). Drains any queued notifications so
        an approver agent can ``cast_approval_vote`` here. Returns the reply text
        in ``TurnResult.text`` for the caller to put on the A2A reply envelope.
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
            notify_block, extra_tools, pending_notes = await self._pending_context_and_tools(agent)
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
            )
            await self._audit(agent, None, "agent.turn_finished", {"mode": "a2a", "tool_rounds": rounds})
            await self._db.commit()
            return TurnResult(status="completed", text=final_text, tool_rounds=rounds)
        except Exception as exc:
            _log.exception("a2a turn failed agent=%s", agent_id)
            await self._db.rollback()
            try:
                await self._audit(agent, None, "agent.turn_failed", {"mode": "a2a", "error": _err_kind(exc)})
                await self._db.commit()
            except Exception:
                _log.exception("a2a turn failure-path bookkeeping failed")
            # The agent never saw the drained notifications — put them back.
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

    async def _stage_workspace_inputs(self, agent: Agent, chatroom_id: uuid.UUID) -> str | None:
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

            # --- triggering message's attachments (existing) ---
            facade = ConversationFacade(self._db)
            attachments = await facade.latest_user_attachments(chatroom_id)
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

        manifest_sha = hashlib.sha256(
            "\n".join(sorted(f"{wf.path}:{wf.sha256}" for wf in ws_files)).encode()
        ).hexdigest()

        total = 0
        chosen = []
        for wf in ws_files:
            if total + wf.size_bytes > _MAX_AGENT_FILES_BYTES:
                break
            total += wf.size_bytes
            chosen.append(wf)
        if not chosen:
            return

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
        self, agent: Agent
    ) -> tuple[str | None, list[Tool], list[dict[str, Any]]]:
        """Drain queued A2A notifications for this agent into a context block
        (R9.16) and, for approval-request notifications, the ``cast_approval_vote``
        tool scoped to exactly the pending gate ids. Best-effort: a Redis hiccup
        yields no context rather than failing the turn.

        Also returns the raw drained notes so a turn that fails (or skips)
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
        approvals: dict[uuid.UUID, uuid.UUID | None] = {}
        lines: list[str] = []
        for n in notes:
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
            else:
                lines.append(f"- {json.dumps(n, separators=(',', ':'))}")
        tools: list[Tool] = []
        if approvals:
            tools.append(
                build_cast_approval_vote_tool(self._db, agent_id=agent.id, allowed_approvals=approvals)
            )
        return "[Incoming notifications]\n" + "\n".join(lines), tools, notes

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
    ) -> TurnResult:
        agent = await AgentsFacade(self._db).get_agent(agent_id)
        if agent is None:
            # An explicit @mention to a now-deleted agent deserves feedback;
            # autonomous triggers (every_n/silence) stay silent.
            if trigger == "mention":
                await emit_agent_finished_error(chatroom_id, agent_id, "agent_gone")
            return TurnResult(status="skipped", reason="agent_gone")
        # AuthZ tap: re-validate the agent↔room binding at turn start (defends
        # against an unbind racing the trigger).
        bound = await ChatroomAgentRepository(self._db).is_registered(
            chatroom_id=chatroom_id, agent_id=agent_id
        )
        if not bound:
            if trigger == "mention":
                await emit_agent_finished_error(chatroom_id, agent_id, "not_bound")
            return TurnResult(status="skipped", reason="not_bound")
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
            await emit_agent_finished_error(chatroom_id, agent.id, "rate_limited")
            return TurnResult(status="skipped", reason="rate_limited")

        provider = agent.model_hint.value
        models = _resolve_models(agent)
        context_limit = _CONTEXT_LIMITS.get(provider, 128_000)

        room = room_channel(chatroom_id)
        pending_notes: list[dict[str, Any]] = []

        try:
            # Emitted inside the try so any failure still routes to the
            # finished/turn_failed path — the room never stays "thinking".
            await self._audit(agent, chatroom_id, "agent.turn_started", {"trigger": trigger})
            await Publisher(room).emit("agent.thinking", {"agent_id": str(agent.id)})

            # Prompt resolution (R9.04–R9.08).
            base_system, lazy_prompt, section_cache = self._resolve_prompt(agent)

            # History + optional compaction (R9.09–R9.11).
            history = await self._assemble_history(agent, chatroom_id, context_limit, models)

            # Context blocks fold into the system prompt (providers take system
            # as a top-level field, not an in-array role).
            system_parts = [base_system] if base_system else []
            for hm in history:
                if hm.role == "system":  # compact_summary
                    system_parts.append(f"[Earlier conversation summary]\n{hm.content}")
            # Retrieval keys off the *current* input when this turn carries one
            # (run_input_turn); otherwise the latest user message in history.
            knowledge_queries = _knowledge_queries(history, input_text=input_text)
            rag_ctx = await self._rag_context(agent, knowledge_queries)
            if rag_ctx:
                system_parts.append(rag_ctx.block)
            graphrag_block = await self._graphrag_context(agent, knowledge_queries)
            if graphrag_block:
                system_parts.append(graphrag_block)
            # Drain queued A2A notifications (R9.16); approval requests also add
            # the cast_approval_vote tool for this turn.
            notify_block, extra_tools, pending_notes = await self._pending_context_and_tools(agent)
            # code_exec artifacts (charts/files) produced this turn land here and
            # are attached to the reply after it's persisted (Code Interpreter).
            artifact_sink: list[dict[str, Any]] = []
            extra_tools = extra_tools + await self._builtin_tools(
                agent, chatroom_id=chatroom_id, artifact_sink=artifact_sink
            )
            # Stage the triggering message's uploads into the kernel workspace so
            # code_exec can read them; the returned note tells the model the paths.
            staged_note = await self._stage_workspace_inputs(agent, chatroom_id)
            if staged_note:
                system_parts.append(staged_note)
            if notify_block:
                system_parts.append(notify_block)

            # Label history with sender names so the agent can tell participants
            # apart (humans by display name, other agents by their configured
            # name). The running agent's own turns stay unlabelled so the model
            # is not trained to echo a "Name:" prefix on its reply.
            agent_names, user_names = await self._participant_labels(agent, chatroom_id, history)
            # Attachments on the triggering user message become content blocks so
            # the agent can see the file. When this turn carries fresh `input_text`,
            # the trigger is that appended message (below); otherwise it's the
            # latest user message already in history.
            attach_blocks = await self._model_attachment_blocks(chatroom_id)
            history_attach_id = (
                next((h.id for h in reversed(history) if h.role == "user"), None)
                if attach_blocks and not input_text
                else None
            )
            messages: list[dict[str, Any]] = [
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
                hm.role == "agent" and hm.sender_id not in (None, agent.id) and hm.sender_id in agent_names
                for hm in history
            )
            # Emit the explanatory note whenever ANY turn will carry a "Name:"
            # prefix. _provider_message prefixes every resolved user/agent label,
            # so gating the note on >1 user left a single-human room with labelled
            # turns but no note — the model then treats "Alice:" as literal text.
            labels_applied = other_agents_present or bool(user_names)
            if labels_applied:
                system_parts.append(_PARTICIPANT_LABEL_NOTE)
            system_text = "\n\n".join(p for p in system_parts if p)

            if input_text:
                if attach_blocks:
                    messages.append(
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": input_text}, *attach_blocks],
                        }
                    )
                else:
                    messages.append({"role": "user", "content": input_text})
            if not messages:
                await Publisher(room).emit("agent.finished", {"agent_id": str(agent.id)})
                await self._audit(agent, chatroom_id, "agent.turn_finished", {"empty": True})
                await self._db.commit()
                self._compact_forced_rooms.discard(chatroom_id)
                # The drained notifications were folded into a prompt that will
                # never reach the provider — restore them for the next turn.
                await self._requeue_notifications(agent, pending_notes)
                return TurnResult(status="skipped", reason="no_input")

            registry = build_registry(
                self._db,
                agent_id=agent.id,
                lazy_prompt=lazy_prompt,
                section_cache=section_cache,
                extra=extra_tools,
            )

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
                await Publisher(room).emit(
                    "agent.finished", {"reason": "empty_reply", "agent_id": str(agent.id)}
                )
                return TurnResult(status="skipped", reason="empty_reply", tool_rounds=rounds)

            reply_meta: dict[str, Any] = {"trigger": trigger, "tool_rounds": rounds}
            if rag_ctx and rag_ctx.sources:
                # Persist what RAG retrieved so the UI can cite it (R10.09).
                reply_meta["rag_sources"] = rag_ctx.sources
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
            return TurnResult(status="completed", message_id=msg.id, text=final_text, tool_rounds=rounds)

        except Exception as exc:
            _log.exception("agent turn failed agent=%s room=%s", agent_id, chatroom_id)
            await self._db.rollback()
            # Never leave the room stuck in "thinking". The WS emit and the
            # audit row are independently guarded: a Redis outage must not
            # swallow the agent.turn_failed audit (DB), and vice versa.
            try:
                await Publisher(room).emit(
                    "agent.finished", {"error": _err_kind(exc), "agent_id": str(agent.id)}
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
        if attachment_blocks:
            text_blocks = [{"type": "text", "text": content}] if content else []
            return {"role": "user", "content": text_blocks + attachment_blocks}
        return {"role": "user", "content": content}

    async def _model_attachment_blocks(self, chatroom_id: uuid.UUID) -> list[dict[str, Any]]:
        """Neutral content blocks for the triggering message's attachments, so
        the agent can see uploaded images/PDFs/text. Best-effort — a storage or
        decode fault must never abort the turn."""
        try:
            facade = ConversationFacade(self._db)
            attachments = await facade.latest_user_attachments(chatroom_id)
            if not attachments:
                return []
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
    ) -> list[tx.HistoryMessage]:
        history = await tx.load_model_history(self._db, chatroom_id=chatroom_id)
        projected = sum(h.token_count for h in history)
        # A POST /compact sets a one-shot flag (G.10); honour it regardless of
        # mode/cap by capping at half the current projection so a range is shed.
        forced = await self._consume_compact_flag(chatroom_id)
        if forced:
            cap: int | None = max(1, projected // 2)
        elif agent.context_mode.value == "compact" and ctxmod.should_compact(
            mode="compact",
            projected_tokens=projected,
            context_token_cap=agent.context_token_cap,
            provider_context_limit=context_limit,
        ):
            cap = agent.context_token_cap
        else:
            return history
        summariser = RouterSummariser(
            router=self._router,
            key_group_id=agent.key_group_id,
            models=models,
            agent_id=agent.id,
        )
        store = tx.MessagesTranscriptStore(self._db, chatroom_id=chatroom_id)
        try:
            did = await ctxmod.run_compact(
                # HistoryMessage structurally satisfies MessageLike; the cast
                # bridges frozen-dataclass attrs vs the Protocol's writable ones.
                messages=cast("list[ctxmod.MessageLike]", history),
                projected_tokens=projected,
                context_token_cap=cap,
                provider_context_limit=context_limit,
                summariser=summariser,
                store=store,
            )
        except ctxmod.CompactFailed as exc:
            # R9.11 — keep the un-compacted history and audit; do not fail the turn.
            _log.warning("compaction failed agent=%s: %s", agent.id, exc)
            await self._audit(agent, chatroom_id, "agent.compact_failed", {"error": str(exc)})
            return history
        if not did:
            # Compaction was a no-op — restore the one-shot flag that
            # _consume_compact_flag removed via GETDEL so a subsequent
            # turn can retry (the user's POST /compact intent is preserved).
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
    ) -> tuple[str, int]:
        tool_specs = registry.specs()
        last_text = ""
        for rounds in range(1, MAX_TOOL_ROUNDS + 1):
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

    async def _rag_context(self, agent: Agent, queries: Sequence[str]) -> RagContext | None:
        """Delegate to the knowledge-context :class:`RagContextProvider`."""
        return await self._rag_provider.query(
            rag_config_id=agent.rag_config_id,
            query_texts=queries,
            agent_id=agent.id,
        )

    async def _graphrag_context(self, agent: Agent, queries: Sequence[str]) -> str | None:
        """Delegate to the knowledge-context :class:`GraphRagContextProvider`."""
        return await self._graphrag_provider.query(
            graphrag_config_id=agent.graphrag_config_id,
            query_texts=queries,
        )

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


__all__ = ["MAX_TOOL_ROUNDS", "TurnEngine", "TurnResult"]
