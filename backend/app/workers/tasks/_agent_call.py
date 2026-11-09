"""One tool-less chat call to a project agent, billed to that agent's key group.

Shared by the jobs that ask an agent for a structured reply (the activity judge,
the classroom design run). Callers map :class:`AgentCallFailed` onto their own
closed failure vocabulary and parse the returned text themselves.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger


class AgentCallFailure(enum.Enum):
    MISCONFIGURED = "misconfigured"
    UNAVAILABLE = "unavailable"


class AgentCallFailed(Exception):
    def __init__(self, failure: AgentCallFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class AgentTarget:
    agent: Any
    provider: Any
    model: str


async def resolve_agent(agents: Any, agent_id: uuid.UUID, project_id: uuid.UUID | None) -> AgentTarget:
    """The agent and its model, or MISCONFIGURED if it left the project or has no model."""
    from contexts.keys.domain.providers import ApiKeyProvider

    agent = await agents.get_agent(agent_id)
    # Checked when the caller was configured too; this holds if the agent was
    # deleted or moved since.
    if agent is None or agent.project_id != project_id:
        raise AgentCallFailed(AgentCallFailure.MISCONFIGURED)
    provider = ApiKeyProvider(agent.model_hint.value)
    model = agent.model_id
    if not model:
        entry = next((c for c in agents.chat_model_catalog() if c.provider == provider.value), None)
        model = entry.default if entry is not None else None
    if not model:
        raise AgentCallFailed(AgentCallFailure.MISCONFIGURED)
    return AgentTarget(agent=agent, provider=provider, model=model)


async def call_agent_once(
    db: Any,
    agents: Any,
    target: AgentTarget,
    *,
    system: str,
    user: str,
    max_tokens: int,
    usage_context: str,
    log: dict[str, str],
    chatroom_id: uuid.UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """The reply text, or UNAVAILABLE. No tools are offered to the model."""
    from contexts.keys.application.provider_router import ProviderRequest
    from contexts.keys.domain.errors import KeyGroupExhausted
    from contexts.keys.domain.providers import ProviderCapability
    from contexts.keys.infrastructure.adapters import build_router

    payload: dict[str, Any] = {
        "model": target.model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": max_tokens,
        **(extra or {}),
    }
    payload.update(agents.chat_model_capabilities(target.provider.value, target.model))
    request = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload=payload,
        agent_id=target.agent.id,
        chatroom_id=chatroom_id,
        provider=target.provider,
        usage_context=usage_context,
    )
    try:
        result = await build_router(db).call(group_id=target.agent.key_group_id, request=request)
    except KeyGroupExhausted as exc:
        raise AgentCallFailed(AgentCallFailure.UNAVAILABLE) from exc
    except Exception as exc:
        # Only the class: a provider message can echo the prompt.
        logger.bind(**log).warning(f"{usage_context} call failed: {type(exc).__name__}")
        raise AgentCallFailed(AgentCallFailure.UNAVAILABLE) from exc
    text = result.body.get("text") if isinstance(result.body, dict) else None
    if result.http_status != 200 or not isinstance(text, str):
        raise AgentCallFailed(AgentCallFailure.UNAVAILABLE)
    return text


__all__ = ["AgentCallFailed", "AgentCallFailure", "AgentTarget", "call_agent_once", "resolve_agent"]
