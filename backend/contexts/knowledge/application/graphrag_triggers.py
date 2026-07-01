"""GraphRAG build trigger evaluation for post-message dispatch."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from shared_kernel.auth.clients import get_redis

_COUNTER_TTL: Final = 86400 * 7
_TRIGGERED_BY_EVERY_N: Final = "every_n_messages"
_BUILDABLE_STATES: Final = frozenset({BuildState.IDLE, BuildState.FAILED})


@dataclass(frozen=True, slots=True)
class GraphRagBuildTrigger:
    config_id: uuid.UUID
    triggered_by: str


class GraphRagMessageCounter(Protocol):
    async def increment(self, config_id: uuid.UUID) -> int: ...


class RedisGraphRagMessageCounter:
    async def increment(self, config_id: uuid.UUID) -> int:
        r = get_redis()
        key = _message_count_key(config_id)
        count = await r.incr(key)
        await r.expire(key, _COUNTER_TTL)
        return int(count)


async def evaluate_graphrag_message_triggers(
    db: AsyncSession,
    *,
    agent_ids: Sequence[uuid.UUID],
    counter: GraphRagMessageCounter | None = None,
) -> list[GraphRagBuildTrigger]:
    unique_agent_ids = list(dict.fromkeys(agent_ids))
    if not unique_agent_ids:
        return []

    configs = await GraphRagConfigRepository(db).list_for_agents(unique_agent_ids)
    active_counter = counter or RedisGraphRagMessageCounter()
    fired: list[GraphRagBuildTrigger] = []

    for cfg in configs:
        every_n = _every_n_messages(cfg.trigger_config)
        if every_n is None or cfg.last_build_state not in _BUILDABLE_STATES:
            continue
        count = await active_counter.increment(cfg.id)
        if count % every_n == 0:
            fired.append(
                GraphRagBuildTrigger(
                    config_id=cfg.id,
                    triggered_by=_TRIGGERED_BY_EVERY_N,
                )
            )

    return fired


def _every_n_messages(trigger_config: dict[str, Any]) -> int | None:
    raw = trigger_config.get(_TRIGGERED_BY_EVERY_N)
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _message_count_key(config_id: uuid.UUID) -> str:
    return f"graphrag:msg_count:{config_id}"


__all__ = [
    "GraphRagBuildTrigger",
    "GraphRagMessageCounter",
    "RedisGraphRagMessageCounter",
    "evaluate_graphrag_message_triggers",
]
