"""Production Summariser for /compact (K.2, R9.10).

Issues the summarisation call through the agent's **own** Key Group via the
K.1 router. Any failure (exhaustion, non-2xx) propagates — ``context.run_compact``
wraps it into ``CompactFailed`` so the caller keeps the un-compacted history and
audits ``agent.compact_failed`` (R9.11).
"""

from __future__ import annotations

import uuid

from contexts.agents.application.context import MessageLike
from contexts.keys.application.provider_router import ProviderRequest, ProviderRouter
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability

_SYSTEM_PROMPT = (
    "You are a context compaction engine. Summarise the conversation excerpt "
    "below into a compact briefing that preserves decisions made, file paths, "
    "identifiers, commitments, and still-open questions. Omit pleasantries. "
    "Write only the summary."
)


def _render(messages: list[MessageLike]) -> str:
    return "\n\n".join(f"[{m.role}] {m.content}" for m in messages)


class RouterSummariser:
    """Concrete ``Summariser`` (see ``context.Summariser``)."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        key_group_id: uuid.UUID,
        provider: ApiKeyProvider,
        model: str,
        agent_id: uuid.UUID | None = None,
    ) -> None:
        self._router = router
        self._key_group_id = key_group_id
        self._provider = provider
        self._model = model
        self._agent_id = agent_id

    async def summarise(self, messages: list[MessageLike], *, max_tokens: int = 2000) -> str:
        request = ProviderRequest(
            capability=ProviderCapability.LLM_CHAT,
            payload={
                "model": self._model,
                "system": _SYSTEM_PROMPT,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": _render(messages)}],
            },
            agent_id=self._agent_id,
            provider=self._provider,
        )
        result = await self._router.call(group_id=self._key_group_id, request=request)
        if result.http_status != 200:
            raise RuntimeError(f"summariser provider returned HTTP {result.http_status}")
        text = str(result.body.get("text", ""))
        # A 200 carrying blank text is a semantic failure the status check cannot
        # see: adapters build "text" by joining the response's text parts, so a
        # refusal or an all-tool-use reply yields "". Returning it would fold the
        # range behind a summary that says nothing — irreversibly, since the
        # loader then elides those messages forever. Same predicate the reply path
        # uses to never persist an empty agent message.
        if not text.strip():
            raise RuntimeError("summariser provider returned empty text at HTTP 200")
        return text


__all__ = ["RouterSummariser"]
