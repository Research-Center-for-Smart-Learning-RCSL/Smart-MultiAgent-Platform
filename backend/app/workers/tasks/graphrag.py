"""Arq task: graphrag_build — E.7 initial build dispatcher (R11.02–R11.04)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.keys.infrastructure.adapters import build_router
from contexts.knowledge.application.graphrag_builder import GraphRagBuilder
from contexts.knowledge.application.graphrag_ports import DeltaMessage
from contexts.knowledge.domain.graphrag import GraphRagConfig
from contexts.knowledge.infrastructure.embedders import router_embedder_for
from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
from contexts.knowledge.infrastructure.redis_lock import RedisBuildLockStore, RedisSnapshotStore
from contexts.knowledge.infrastructure.triple_extractor import LlmTripleExtractor
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.observability.metrics import GRAPHRAG_BUILD_STATE

_log = logging.getLogger(__name__)

_EMBED_MODEL: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "gemini": "text-embedding-004",
    "voyage": "voyage-3",
}


@dataclass
class _DbMsg:
    id: uuid.UUID
    role: str
    content: str


class _DbDeltaLoader:
    """Load delta messages from chatrooms the agent participates in."""

    def __init__(self, *, agent_id: uuid.UUID) -> None:
        self._agent_id = agent_id

    _BATCH_SIZE = 2000

    async def load(
        self,
        *,
        config_id: Any,
        since: Any,
        mode: Any,
    ) -> list[DeltaMessage]:
        sm = get_sessionmaker()
        result: list[DeltaMessage] = []
        last_id: str | None = None
        async with sm() as db:
            while True:
                # Keyset pagination on m.id (stable PK order within the
                # created_at sort) to avoid loading unbounded result sets.
                id_clause = "AND m.id > :last_id " if last_id else ""
                params: dict[str, Any] = {
                    "agent_id": str(self._agent_id),
                    "since": since,
                    "batch_size": self._BATCH_SIZE,
                }
                if last_id is not None:
                    params["last_id"] = last_id
                rows = (
                    await db.execute(
                        sa.text(
                            "SELECT m.id, m.sender_type AS role, m.content_md AS content "
                            "FROM messages m "
                            "JOIN chatrooms cr ON cr.id = m.chatroom_id "
                            "JOIN chatroom_agents ca ON ca.chatroom_id = cr.id "
                            "WHERE ca.agent_id = :agent_id "
                            "  AND m.deleted_at IS NULL "
                            "  AND (:since::timestamptz IS NULL OR m.created_at > :since) "
                            f"{id_clause}"
                            "ORDER BY m.created_at, m.id "
                            "LIMIT :batch_size"
                        ),
                        params,
                    )
                ).all()
                result.extend(_DbMsg(id=r.id, role=r.role, content=r.content) for r in rows)
                if len(rows) < self._BATCH_SIZE:
                    break
                last_id = str(rows[-1].id)
        return result


async def _resolve_embed_key(
    db: AsyncSession,
    builder_key_group_id: uuid.UUID,
) -> tuple[str, str, uuid.UUID] | None:
    """Return (provider, model, key_id) for the first embedding key in the group.

    No plaintext leaves this function — the router unwraps on demand and pins
    this key for the whole build (stable vector dimensions).
    """
    from contexts.keys.infrastructure.group_repository import (
        KeyGroupMemberRepository,
    )
    from contexts.keys.infrastructure.repositories import ApiKeyRepository

    members = await KeyGroupMemberRepository(db).list_ordered(builder_key_group_id)
    for m in members:
        key = await ApiKeyRepository(db).get_active(m.key_id)
        if key is None:
            continue
        provider = key.provider.value
        if provider not in _EMBED_MODEL:
            continue
        return provider, _EMBED_MODEL[provider], key.id
    return None


def _make_embedder_factory(db: AsyncSession) -> Any:
    """Return an EmbedderFactory resolving a pinned key from the builder group."""
    router = build_router(db)

    async def _factory(cfg: GraphRagConfig) -> Any:
        resolved = await _resolve_embed_key(db, cfg.builder_key_group_id)
        if resolved is None:
            raise RuntimeError(
                f"builder key group {cfg.builder_key_group_id} has no embedding key " "(openai/gemini/voyage)"
            )
        provider, model, key_id = resolved
        return router_embedder_for(router=router, key_id=key_id, provider=provider, model=model)

    return _factory


async def graphrag_build(
    ctx: dict[str, Any],
    *,
    config_id: str,
    triggered_by: str = "manual",
) -> str:
    """Run a full GraphRAG build for one config (2PC, R11.04)."""
    cfg_id = uuid.UUID(config_id)
    settings = get_settings()

    neo4j = Neo4jAsyncDriver(
        uri=settings.neo4j.url,
        auth=(settings.neo4j.user, settings.neo4j.password),
    )
    qclient = AsyncQdrantClient(
        url=settings.qdrant.url,
        api_key=settings.qdrant.api_key or None,
    )
    vector_store = GraphRagVectorStore(qclient)
    lock_store = RedisBuildLockStore()
    snapshot_store = RedisSnapshotStore()

    sm = get_sessionmaker()
    async with sm() as db:
        cfg = await GraphRagConfigRepository(db).get(cfg_id)
        if cfg is None:
            _log.warning("graphrag_build: config %s not found", config_id)
            return f"config {config_id} not found"

        extractor = LlmTripleExtractor(router=build_router(db))
        delta_loader = _DbDeltaLoader(agent_id=cfg.agent_id)

        builder = GraphRagBuilder(
            db=db,
            neo4j=neo4j,
            vector_store=vector_store,
            extractor=extractor,
            lock_store=lock_store,
            snapshot_store=snapshot_store,
            delta_loader=delta_loader,
            embedder_factory=_make_embedder_factory(db),
        )
        cfg_id_str = str(cfg_id)

        # One-hot per state — set the active label to 1 and zero the others so
        # `graphrag_build_state{config_id="...", state="..."} == 1` is unique
        # at any moment.
        def _set_state(active: str) -> None:
            for s in ("idle", "building", "ready", "failed"):
                GRAPHRAG_BUILD_STATE.labels(
                    config_id=cfg_id_str,
                    state=s,
                ).set(1.0 if s == active else 0.0)

        _set_state("building")
        try:
            result = await builder.run(config_id=cfg_id, triggered_by=triggered_by)
            await db.commit()
            _set_state(result.state.value if result.state.value in ("ready", "failed") else "idle")
            _log.info(
                "graphrag_build done config=%s state=%s triples=%d entities=%d",
                config_id,
                result.state.value,
                result.triples_written,
                result.entities_written,
            )
            return (
                f"state={result.state.value} "
                f"triples={result.triples_written} "
                f"entities={result.entities_written}"
            )
        except Exception:
            _set_state("failed")
            _log.exception("graphrag_build failed config=%s", config_id)
            raise
        finally:
            await neo4j.close()
            await qclient.close()


async def graphrag_reconcile(ctx: dict[str, Any]) -> int:
    """arq cron tick (M.5.4): heal GraphRAG configs stuck in FAILED_COMPENSATING
    (R11.04 / 2PC drift). Without this scheduled task the reconciler loop was
    never run in production and drift was never repaired. Runs once per minute;
    arq's cron lock keeps it a singleton across worker replicas.
    """
    from app.workers.graphrag_reconciler import reconcile_once

    healed = await reconcile_once()
    if healed:
        _log.info("graphrag reconcile healed %d config(s): %s", len(healed), healed)
    return len(healed)


__all__ = ["graphrag_build", "graphrag_reconcile"]
