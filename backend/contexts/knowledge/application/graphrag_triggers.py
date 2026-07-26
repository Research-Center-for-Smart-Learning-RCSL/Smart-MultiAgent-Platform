"""GraphRAG build trigger evaluation for post-message dispatch."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from shared_kernel.auth.clients import get_redis

_COUNTER_TTL: Final = 86400 * 7
_SILENCE_TS_TTL: Final = 86400 * 7
_TRIGGERED_BY_EVERY_N: Final = "every_n_messages"
_TRIGGERED_BY_SILENCE: Final = "silence_minutes"
# Automatic triggers only. RECOVERY_UNAVAILABLE is deliberately excluded even though
# the builder engine and the manual endpoint accept it: that state means a partially
# applied build sits in Neo4j with no possible rollback, and silently rebuilding over
# it on a message-count or silence timer would hide the inconsistency instead of
# surfacing it. Recovery from that state must be a deliberate human action.
_BUILDABLE_STATES: Final = frozenset({BuildState.IDLE, BuildState.FAILED})


def graphrag_build_job_id(
    config_id: uuid.UUID,
    *,
    last_build_state: BuildState,
    last_build_at: datetime | None,
) -> str:
    """Stable per-rebuild-cycle arq job id for build de-duplication (D5).

    Every trigger fired between two builds resolves to the same id, so
    concurrent turns collapse to a single queued ``graphrag_build`` (arq returns
    ``None`` for a duplicate id, and the loser never burns a worker slot). The
    nonce — the config's terminal state plus its ``last_build_at`` epoch —
    changes once a build completes (``last_build_at`` advances to the started-at
    watermark) or the state flips idle<->failed, so a legitimate rebuild is never
    suppressed by the prior job's retained result (``keep_result``). ``0`` stands
    in for a config that has never built.
    """
    epoch = int(last_build_at.timestamp()) if last_build_at is not None else 0
    return f"graphrag:build:{config_id}:{last_build_state.value}:{epoch}"


@dataclass(frozen=True, slots=True)
class GraphRagBuildTrigger:
    config_id: uuid.UUID
    triggered_by: str
    job_id: str


class GraphRagMessageCounter(Protocol):
    async def increment(self, config_id: uuid.UUID) -> int: ...


class RedisGraphRagMessageCounter:
    async def increment(self, config_id: uuid.UUID) -> int:
        r = get_redis()
        key = _message_count_key(config_id)
        pipe = r.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, _COUNTER_TTL)
        results = await pipe.execute()
        return int(results[0])


class GraphRagSilenceClock(Protocol):
    """Per-config last-activity timestamps for the silence-trigger sweep (F-4).

    Batched by design: a message send advances the timer for every covering
    config in one round-trip, and the sweep reads a whole page at once, so
    neither the hot send path nor the cron pays one Redis call per config.
    """

    async def touch_many(self, config_ids: Sequence[uuid.UUID]) -> None: ...
    async def get_many(self, config_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, datetime | None]: ...


class RedisGraphRagSilenceClock:
    """Redis-backed ``graphrag:silence_ts:{config_id}`` — the analogue of the
    wake-up ``wakeup:silence_ts`` timer, keyed per config (not per room) so a
    workspace/agent_group map whose coverage spans many rooms is "silent" only
    when its *entire* coverage has gone quiet."""

    async def touch_many(self, config_ids: Sequence[uuid.UUID]) -> None:
        if not config_ids:
            return
        ts = datetime.now(UTC).isoformat()
        pipe = get_redis().pipeline(transaction=False)
        for config_id in config_ids:
            pipe.set(_silence_ts_key(config_id), ts, ex=_SILENCE_TS_TTL)
        await pipe.execute()

    async def get_many(self, config_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, datetime | None]:
        if not config_ids:
            return {}
        raws = await get_redis().mget([_silence_ts_key(cid) for cid in config_ids])
        return {
            cid: (datetime.fromisoformat(str(raw)) if raw else None)
            for cid, raw in zip(config_ids, raws, strict=True)
        }


async def evaluate_graphrag_message_triggers(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    counter: GraphRagMessageCounter | None = None,
    activity: GraphRagSilenceClock | None = None,
) -> list[GraphRagBuildTrigger]:
    # F-3: resolve configs by room coverage (chatroom / enabled agent_group /
    # enabled workspace), NOT the agent-delete cascade ``list_for_agents`` — that
    # selector omitted chatroom/workspace owners and multi-member groups, so
    # message-count builds never fired for them.
    #
    # R11.02/R11.08: the room alone scopes coverage. An agentless room still has
    # chatroom- and workspace-owned maps to count for, so there is no Agent
    # precondition to check here; the selector derives group membership itself.
    configs = await GraphRagConfigRepository(db).list_message_trigger_configs_for_room(
        chatroom_id=chatroom_id
    )
    active_counter = counter or RedisGraphRagMessageCounter()
    active_activity = activity or RedisGraphRagSilenceClock()

    # F-4: this send is activity across the whole covering set, so advance the
    # silence timer for every covering config that declares a silence trigger —
    # in a single pipelined round-trip, not one Redis write per config on the hot
    # send path. Configs without ``silence_minutes`` are never enumerated by the
    # sweep, so touching their timer would only be a wasted write.
    silence_ids = [cfg.id for cfg in configs if _silence_minutes(cfg.trigger_config) is not None]
    if silence_ids:
        await active_activity.touch_many(silence_ids)

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
                    job_id=graphrag_build_job_id(
                        cfg.id,
                        last_build_state=cfg.last_build_state,
                        last_build_at=cfg.last_build_at,
                    ),
                )
            )

    return fired


def evaluate_graphrag_silence_trigger(
    cfg: GraphRagConfig,
    *,
    last_activity_at: datetime | None,
    now: datetime,
) -> GraphRagBuildTrigger | None:
    """Return a silence build trigger for one config, or ``None`` (F-4).

    Fires when the config's coverage has been idle for at least
    ``silence_minutes``: the config declares ``silence_minutes``, is in a
    buildable state, has a recorded last-activity timestamp, and
    ``now - last_activity >= silence_minutes``. A config that has never seen
    activity (no timestamp) never fires.

    Re-fire discipline (AC-3) — a build fires at most once per idle cycle,
    without any extra Redis state:

    - the buildable-state gate stops re-evaluation while the fired build RUNs;
    - the freshness gate (``last_activity_at`` must be newer than
      ``last_build_at``) stops a rebuild once the build completes and its
      started-at watermark advances past the last activity — so a still-idle room
      is not rebuilt again until new activity arrives;
    - the stable ``graphrag_build_job_id`` (keyed on state + ``last_build_at``)
      collapses any sweep ticks in the window before the build starts onto one
      queued job.
    """
    minutes = _silence_minutes(cfg.trigger_config)
    if minutes is None or cfg.last_build_state not in _BUILDABLE_STATES:
        return None
    if last_activity_at is None:
        return None
    # Nothing new since the last build already captured the corpus up to then.
    if cfg.last_build_at is not None and last_activity_at <= cfg.last_build_at:
        return None
    if (now - last_activity_at).total_seconds() < minutes * 60:
        return None
    return GraphRagBuildTrigger(
        config_id=cfg.id,
        triggered_by=_TRIGGERED_BY_SILENCE,
        job_id=graphrag_build_job_id(
            cfg.id,
            last_build_state=cfg.last_build_state,
            last_build_at=cfg.last_build_at,
        ),
    )


def _every_n_messages(trigger_config: dict[str, Any]) -> int | None:
    raw = trigger_config.get(_TRIGGERED_BY_EVERY_N)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _silence_minutes(trigger_config: dict[str, Any]) -> int | None:
    raw = trigger_config.get(_TRIGGERED_BY_SILENCE)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _message_count_key(config_id: uuid.UUID) -> str:
    return f"graphrag:msg_count:{config_id}"


def _silence_ts_key(config_id: uuid.UUID) -> str:
    return f"graphrag:silence_ts:{config_id}"


__all__ = [
    "GraphRagBuildTrigger",
    "GraphRagMessageCounter",
    "GraphRagSilenceClock",
    "RedisGraphRagMessageCounter",
    "RedisGraphRagSilenceClock",
    "evaluate_graphrag_message_triggers",
    "evaluate_graphrag_silence_trigger",
    "graphrag_build_job_id",
]
