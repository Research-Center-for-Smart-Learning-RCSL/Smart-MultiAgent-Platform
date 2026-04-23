"""Arq task: graphrag_build — E.7 initial build dispatcher (R11.02–R11.04)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from qdrant_client import AsyncQdrantClient

from app.config.settings import get_settings
from contexts.knowledge.application.graphrag_builder import GraphRagBuilder
from contexts.knowledge.application.graphrag_ports import DeltaMessage
from contexts.knowledge.domain.graphrag import GraphRagConfig
from contexts.knowledge.infrastructure.chat_completer import HttpChatCompleter
from contexts.knowledge.infrastructure.embedders import embedder_for
from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
from contexts.knowledge.infrastructure.redis_lock import RedisBuildLockStore, RedisSnapshotStore
from contexts.knowledge.infrastructure.triple_extractor import LlmTripleExtractor
from shared_kernel.db.session import get_sessionmaker

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

    async def load(
        self, *, config_id: Any, since: Any, mode: Any,
    ) -> list[DeltaMessage]:
        sm = get_sessionmaker()
        async with sm() as db:
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
                        "ORDER BY m.created_at"
                    ),
                    {"agent_id": str(self._agent_id), "since": since},
                )
            ).all()
        return [_DbMsg(id=r.id, role=r.role, content=r.content) for r in rows]


async def _resolve_embed_key(
    builder_key_group_id: uuid.UUID,
) -> tuple[str, str, str] | None:
    """Return (provider, model, plaintext_api_key) for the first embedding key in the group."""
    from contexts.keys.infrastructure.group_repository import (  # noqa: PLC0415
        KeyGroupMemberRepository,
    )
    from contexts.keys.infrastructure.repositories import ApiKeyRepository  # noqa: PLC0415
    from contexts.keys.interfaces.facade import KeysFacade  # noqa: PLC0415

    sm = get_sessionmaker()
    async with sm() as db:
        members = await KeyGroupMemberRepository(db).list_ordered(builder_key_group_id)
        for m in members:
            key = await ApiKeyRepository(db).get_active(m.key_id)
            if key is None:
                continue
            provider = key.provider.value
            if provider not in _EMBED_MODEL:
                continue
            plaintext = await KeysFacade(db).unwrap_api_key_plaintext(key.id)
            try:
                return provider, _EMBED_MODEL[provider], plaintext.decode("utf-8")
            finally:
                plaintext = b"\x00" * len(plaintext)  # noqa: F841
    return None


def _make_embedder_factory():
    """Return an EmbedderFactory that resolves from the builder key group."""
    async def _factory(cfg: GraphRagConfig):
        resolved = await _resolve_embed_key(cfg.builder_key_group_id)
        if resolved is None:
            raise RuntimeError(
                f"builder key group {cfg.builder_key_group_id} has no embedding key "
                "(openai/gemini/voyage)"
            )
        provider, model, api_key = resolved
        return embedder_for(provider=provider, model=model, api_key=api_key)

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

        extractor = LlmTripleExtractor(db=db, completer=HttpChatCompleter())
        delta_loader = _DbDeltaLoader(agent_id=cfg.agent_id)

        builder = GraphRagBuilder(
            db=db,
            neo4j=neo4j,
            vector_store=vector_store,
            extractor=extractor,
            lock_store=lock_store,
            snapshot_store=snapshot_store,
            delta_loader=delta_loader,
            embedder_factory=_make_embedder_factory(),
        )
        try:
            result = await builder.run(config_id=cfg_id, triggered_by=triggered_by)
            await db.commit()
            _log.info(
                "graphrag_build done config=%s state=%s triples=%d entities=%d",
                config_id, result.state.value, result.triples_written,
                result.entities_written,
            )
            return (
                f"state={result.state.value} "
                f"triples={result.triples_written} "
                f"entities={result.entities_written}"
            )
        except Exception:
            _log.exception("graphrag_build failed config=%s", config_id)
            raise
        finally:
            await neo4j.close()
            await qclient.close()


__all__ = ["graphrag_build"]
