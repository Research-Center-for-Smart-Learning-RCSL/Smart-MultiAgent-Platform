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

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application import context as ctxmod
from contexts.agents.application.prompt_loader import LazyPrompt, SectionCache, assemble
from contexts.agents.application.runtime import transcript as tx
from contexts.agents.application.runtime.summariser import RouterSummariser
from contexts.agents.application.runtime.tool_registry import (
    Tool,
    build_cast_approval_vote_tool,
    build_registry,
)
from contexts.agents.domain.models import Agent
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.application.message_service import MessageService
from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository
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
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher, room_channel
from shared_kernel.realtime.turn_lock import turn_lock

_log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
_DEFAULT_MAX_TOKENS = 4096

# The schema stores only a provider *hint* (`model_hint`), not a concrete model.
# These are the turn-engine's caller-supplied chat-model defaults per provider —
# passed to the adapter as a `models` map (not an adapter hardcode), so whichever
# key the router rotates to gets a model. (Schema gap: agents have no model id.)
_DEFAULT_CHAT_MODELS: dict[str, str] = {
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
}
_CONTEXT_LIMITS: dict[str, int] = {
    "claude": 200_000,
    "openai": 128_000,
    "gemini": 1_000_000,
}


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
        async with turn_lock(agent_id, chatroom_id) as acquired:
            if not acquired:
                # A turn is already running for this (agent, room); coalescing of
                # the missed trigger is the caller's concern (K.3).
                return TurnResult(status="skipped", reason="locked")
            return await self._run_locked(
                agent_id=agent_id,
                chatroom_id=chatroom_id,
                trigger=trigger,
                parent_agent_id=parent_agent_id,
                input_text=input_text,
                request_id=request_id,
            )

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
        models = dict(_DEFAULT_CHAT_MODELS)
        wf = str(workflow_run_id) if workflow_run_id else None
        try:
            await self._audit(agent, None, "agent.turn_started", {"mode": "a2a", "workflow_run_id": wf})
            base_system, lazy_prompt, section_cache = self._resolve_prompt(agent)
            system_parts = [base_system] if base_system else []
            notify_block, extra_tools = await self._pending_context_and_tools(agent)
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
            return TurnResult(status="failed", reason=_err_kind(exc))

    async def _builtin_tools(self, agent: Agent) -> list[Tool]:
        """Assemble the sandbox (``code_exec`` / ``file``) + ``web_search`` built-in
        tools and the agent's bound MCP tools for this turn (K.5).

        Best-effort: a wiring fault (no Docker daemon in a dev run, etc.) must
        not abort the turn — the agent simply runs without those tools. Each
        tool's own ``invoke`` already degrades a runtime fault to an ``is_error``
        result, so this guard only covers assembly itself."""
        try:
            from contexts.agents.application.runtime.builtin_tools import (
                build_builtin_tools,
                default_builtin_deps,
            )

            bindings = await AgentsFacade(self._db).list_mcp_bindings(agent.id)
            return build_builtin_tools(
                self._db, agent=agent, mcp_bindings=list(bindings), deps=default_builtin_deps()
            )
        except Exception:
            _log.warning("builtin tool assembly failed for agent %s", agent.id, exc_info=True)
            return []

    def _resolve_prompt(
        self, agent: Agent
    ) -> tuple[str, LazyPrompt | None, SectionCache | None]:
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

    async def _pending_context_and_tools(self, agent: Agent) -> tuple[str | None, list[Tool]]:
        """Drain queued A2A notifications for this agent into a context block
        (R9.16) and, for approval-request notifications, the ``cast_approval_vote``
        tool scoped to exactly the pending gate ids. Best-effort: a Redis hiccup
        yields no context rather than failing the turn."""
        from contexts.orchestration.infrastructure import pending_notify

        try:
            notes = await pending_notify.drain(agent.id)
        except Exception:
            return None, []
        if not notes:
            return None, []
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
            else:
                lines.append(f"- {json.dumps(n, separators=(',', ':'))}")
        tools: list[Tool] = []
        if approvals:
            tools.append(
                build_cast_approval_vote_tool(
                    self._db, agent_id=agent.id, allowed_approvals=approvals
                )
            )
        return "[Incoming notifications]\n" + "\n".join(lines), tools

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
            return TurnResult(status="skipped", reason="agent_gone")
        # AuthZ tap: re-validate the agent↔room binding at turn start (defends
        # against an unbind racing the trigger).
        bound = await ChatroomAgentRepository(self._db).is_registered(
            chatroom_id=chatroom_id, agent_id=agent_id
        )
        if not bound:
            return TurnResult(status="skipped", reason="not_bound")

        provider = agent.model_hint.value
        models = dict(_DEFAULT_CHAT_MODELS)
        context_limit = _CONTEXT_LIMITS.get(provider, 128_000)

        room = room_channel(chatroom_id)

        try:
            # Emitted inside the try so any failure still routes to the
            # finished/turn_failed path — the room never stays "thinking".
            await self._audit(agent, chatroom_id, "agent.turn_started", {"trigger": trigger})
            await Publisher(room).emit("agent.thinking")

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
            rag_block = await self._rag_context(agent, history)
            if rag_block:
                system_parts.append(rag_block)
            # Drain queued A2A notifications (R9.16); approval requests also add
            # the cast_approval_vote tool for this turn.
            notify_block, extra_tools = await self._pending_context_and_tools(agent)
            extra_tools = extra_tools + await self._builtin_tools(agent)
            if notify_block:
                system_parts.append(notify_block)
            system_text = "\n\n".join(p for p in system_parts if p)

            messages: list[dict[str, Any]] = [
                {"role": "assistant" if hm.role == "agent" else "user", "content": hm.content}
                for hm in history
                if hm.role in ("user", "agent")
            ]
            if input_text:
                messages.append({"role": "user", "content": input_text})
            if not messages:
                await Publisher(room).emit("agent.finished")
                await self._audit(agent, chatroom_id, "agent.turn_finished", {"empty": True})
                await self._db.commit()
                return TurnResult(status="skipped", reason="no_input")

            registry = build_registry(
                self._db,
                agent_id=agent.id,
                lazy_prompt=lazy_prompt,
                section_cache=section_cache,
                extra=extra_tools,
            )

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

            msg = await MessageService(self._db).send_agent(
                chatroom_id=chatroom_id,
                agent_id=agent.id,
                content_md=final_text,
                metadata={"trigger": trigger, "tool_rounds": rounds},
                request_id=request_id,
            )
            await self._audit(
                agent, chatroom_id, "agent.turn_finished", {"tool_rounds": rounds}
            )
            await self._db.commit()
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
            await pub.emit("agent.finished", {"message_id": str(msg.id)})
            return TurnResult(
                status="completed", message_id=msg.id, text=final_text, tool_rounds=rounds
            )

        except Exception as exc:
            _log.exception("agent turn failed agent=%s room=%s", agent_id, chatroom_id)
            await self._db.rollback()
            # Never leave the room stuck in "thinking".
            try:
                await Publisher(room).emit("agent.finished", {"error": _err_kind(exc)})
                await self._audit(
                    agent, chatroom_id, "agent.turn_failed", {"error": _err_kind(exc)}
                )
                await self._db.commit()
            except Exception:
                _log.exception("agent turn failure-path bookkeeping failed")
            return TurnResult(status="failed", reason=_err_kind(exc))

    # ----------------------------------------------------------------- #

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
            return history
        # Reload so the summary replaces the folded range.
        return await tx.load_model_history(self._db, chatroom_id=chatroom_id)

    async def _consume_compact_flag(self, chatroom_id: uuid.UUID) -> bool:
        """Read-and-clear the forced-compaction flag set by POST /compact."""
        try:
            from shared_kernel.auth.clients import get_redis

            redis = get_redis()
            key = f"compact:pending:{chatroom_id}"
            val = await redis.get(key)
            if val:
                await redis.delete(key)
                return True
            return False
        except Exception:
            return False

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
            request = ProviderRequest(
                capability=ProviderCapability.LLM_CHAT,
                payload=payload,
                agent_id=agent.id,
                parent_agent_id=parent_agent_id,
                chatroom_id=chatroom_id,
            )
            body: dict[str, Any] = {}
            async for ev in self._router.call_stream(
                group_id=agent.key_group_id, request=request
            ):
                if isinstance(ev, TokenDelta):
                    if room is not None:
                        await Publisher(room).emit("agent.token", {"text": ev.text})
                elif isinstance(ev, StreamComplete):
                    body = ev.result.body

            last_text = str(body.get("text", ""))
            tool_calls = body.get("tool_calls") or []
            if not tool_calls:
                return last_text, rounds - 1
            # Append the assistant tool-use turn, then each tool result.
            messages.append(
                {"role": "assistant", "content": last_text, "tool_calls": tool_calls}
            )
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
        # Tool-round budget exhausted — return the latest text we have.
        return last_text, MAX_TOOL_ROUNDS

    async def _rag_context(
        self, agent: Agent, history: list[tx.HistoryMessage]
    ) -> str | None:
        if agent.rag_config_id is None or self._qdrant_url is None:
            return None
        query = next(
            (h.content for h in reversed(history) if h.role == "user"), None
        )
        if not query:
            return None
        try:
            from qdrant_client import AsyncQdrantClient

            from contexts.knowledge.application.retrieve import RetrieveService
            from contexts.knowledge.infrastructure.embedders import router_embedder_for
            from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
            from contexts.knowledge.infrastructure.repositories import RagConfigRepository

            cfg = await RagConfigRepository(self._db).get(agent.rag_config_id)
            if cfg is None or cfg.embed_key_id is None:
                return None
            embedder = router_embedder_for(
                router=self._router,
                key_id=cfg.embed_key_id,
                provider=cfg.embed_provider,
                model=cfg.embed_model,
            )
            qclient = AsyncQdrantClient(url=self._qdrant_url, api_key=self._qdrant_api_key or None)
            try:
                svc = RetrieveService(self._db, embedder=embedder, qdrant=QdrantStore(qclient))
                chunks = await svc.query(
                    config_id=agent.rag_config_id, text=query, agent_id=agent.id, rerank=False
                )
                if not chunks:
                    return None
                return str(svc.format_as_rag_message(chunks)["content"])
            finally:
                await qclient.close()
        except Exception:
            _log.warning("RAG retrieval failed agent=%s", agent.id, exc_info=True)
            return None

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


__all__ = ["MAX_TOOL_ROUNDS", "TurnEngine", "TurnResult"]
