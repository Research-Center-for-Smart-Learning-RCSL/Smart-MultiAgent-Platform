"""Arq task: prompt_assistant_turn — §29 streaming assistant reply.

Runs off the request: resolves the effective config, assembles the context
(fixed wrapper + configurer prompt + inlined reference-file text + session
history + current editor draft), calls the provider router's pinned-key
streaming path, and publishes ``prompt.token`` / ``prompt.finished`` /
``prompt.error`` events on the session's Redis channel for the WS route to fan
out. Provider calls execute only here, never in the web process (R29.08).
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import Any

from contexts.keys.application.provider_router import ProviderRequest, StreamComplete, TokenDelta
from contexts.keys.domain.providers import ProviderCapability
from contexts.keys.infrastructure.adapters import build_router
from contexts.prompt_studio.application.config_service import ConfigService
from contexts.prompt_studio.application.prompts import build_system_text
from contexts.prompt_studio.domain.models import ASSISTANT_MAX_TOKENS, ScanStatus, SessionMessage
from contexts.prompt_studio.infrastructure.channels import prompt_assistant_channel
from contexts.prompt_studio.infrastructure.repositories import AssistantConfigRepository
from contexts.prompt_studio.infrastructure.session_store import SessionStore
from shared_kernel.auth.clients import get_redis
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)


async def prompt_assistant_turn(ctx: dict[str, Any], session_id: str, editor_draft: str) -> str:
    _ = ctx
    sid = uuid.UUID(session_id)
    channel = prompt_assistant_channel(sid)
    publisher = Publisher(channel)
    store = SessionStore(get_redis())

    session = await store.get(sid)
    if session is None:
        with contextlib.suppress(Exception):
            await publisher.emit("prompt.error", {"code": "prompt-studio/session-not-found"})
        return "session not found"

    sm = get_sessionmaker()
    async with sm() as db:
        config = await ConfigService(db).resolve_for_project(
            project_id=session.project_id, user_id=session.user_id
        )
        if config is None or config.key_id is None:
            await publisher.emit(
                "prompt.error",
                {"code": "prompt-studio/unavailable", "message": "assistant is not available"},
            )
            return "no resolvable config / key revoked"
        if not config.model_id:
            await publisher.emit(
                "prompt.error",
                {"code": "prompt-studio/no-model", "message": "no model configured"},
            )
            return "no model configured"

        files = await AssistantConfigRepository(db).list_files(config.id)
        reference_texts = [
            (f.filename, f.extracted_text)
            for f in files
            if f.scan_status is ScanStatus.CLEAN and f.extracted_text
        ]
        system_text = build_system_text(
            config_system_prompt=config.system_prompt,
            reference_texts=reference_texts,
            editor_draft=editor_draft,
        )
        messages = [{"role": m.role, "content": m.content} for m in session.messages]

        request = ProviderRequest(
            capability=ProviderCapability.LLM_CHAT,
            payload={
                "models": [config.model_id],
                "system": system_text,
                "messages": messages,
                "max_tokens": ASSISTANT_MAX_TOKENS,
            },
            usage_context="prompt_assistant",
        )

        router = build_router(db)
        parts: list[str] = []
        try:
            async for ev in router.call_single_key_stream(key_id=config.key_id, request=request):
                if isinstance(ev, TokenDelta):
                    parts.append(ev.text)
                    await publisher.emit("prompt.token", {"text": ev.text})
                elif isinstance(ev, StreamComplete):
                    # Prefer the accumulated body text if the adapter provides it.
                    body_text = ev.result.body.get("text") if isinstance(ev.result.body, dict) else None
                    if body_text:
                        parts = [body_text]
            await db.commit()
        except Exception as exc:  # provider / key / stream failure
            await db.commit()  # persist the usage row recorded by the accountant
            _log.warning("prompt_assistant_turn failed session=%s err=%s", session_id, exc)
            with contextlib.suppress(Exception):
                await publisher.emit(
                    "prompt.error",
                    {"code": "prompt-studio/turn-failed", "message": "the assistant call failed"},
                )
            return "turn failed"

    reply = "".join(parts)
    with contextlib.suppress(Exception):
        await store.append_message(session, SessionMessage(role="assistant", content=reply))
    await publisher.emit("prompt.finished", {"text": reply})
    return "ok"


__all__ = ["prompt_assistant_turn"]
