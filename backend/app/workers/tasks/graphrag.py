"""Arq task: graphrag_build — E.7 initial build dispatcher (R11.02–R11.04)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
from contexts.keys.infrastructure.adapters import build_router
from contexts.knowledge.application.embed_resolution import resolve_pinned_embed_key
from contexts.knowledge.application.graphrag_builder import (
    LOCK_TTL_S,
    EmbedderFactory,
    GraphRagBuilder,
    ResolvedEmbedder,
)
from contexts.knowledge.application.graphrag_ports import ConfigLike, DeltaMessage
from contexts.knowledge.infrastructure.embedders import router_embedder_for
from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
from contexts.knowledge.infrastructure.redis_lock import RedisBuildLockStore, RedisSnapshotStore
from contexts.knowledge.infrastructure.triple_extractor import LlmTripleExtractor
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.observability.metrics import GRAPHRAG_BUILD_STATE

_log = logging.getLogger(__name__)

# D3 (R11.16): the job timeout is only a runaway backstop. The build lock
# (LOCK_TTL_S), refreshed at every window boundary, is the authoritative
# single-writer guard, so the timeout must have comfortable headroom over the
# TTL — otherwise the job could be killed while it still legitimately holds the
# lock. Scoped to graphrag_build via arq's per-function timeout so other lanes
# keep the default worker job_timeout.
GRAPHRAG_BUILD_TIMEOUT_S = LOCK_TTL_S * 3

# D1: build-window bounds. A window is flushed at whichever limit trips first —
# the token budget caps the per-call LLM extraction payload (the real
# constraint), the message cap bounds the DB/keyset step and guarantees forward
# progress on pathological single-message sizes (Q-4). The DB fetch page
# (_BATCH_SIZE) is orthogonal — it is how many rows one query pulls, not the
# build window.
_WINDOW_TOKEN_BUDGET = 24_000
_WINDOW_MSG_CAP = 500


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token); min 1 so every message counts."""
    return max(1, len(text) // 4)


@dataclass
class _DbMsg:
    id: uuid.UUID
    role: str
    content: str
    # Phase 2b (R11.22) — the agent_group member whose room feed surfaced this
    # message; ``None`` for a single-owner / non-agent_group build.
    source_member_id: uuid.UUID | None = None


class _DbDeltaLoader:
    """Load delta messages from chatrooms the owner group's members participate
    in (D1: bounded windows so memory and per-call LLM payload stay flat).

    Phase 2b WS1: an agent_group owner may have several members, so the feed is
    the DISTINCT union of every member agent's room messages (R11.08). A message
    co-present to two members' rooms is ingested exactly once (AC-1), tagged with
    a deterministic ``source_member_id`` — the smallest member agent id sharing
    that message — so extracted relations carry stable member provenance (R11.22).
    """

    def __init__(self, *, member_agent_ids: Sequence[uuid.UUID]) -> None:
        # Deduped so a repeated member never double-expands the IN list.
        self._member_agent_ids = list(dict.fromkeys(member_agent_ids))

    _BATCH_SIZE = 2000

    async def iter_windows(
        self,
        *,
        config_id: Any,
        since: Any,
        mode: Any,
    ) -> AsyncIterator[list[DeltaMessage]]:
        if not self._member_agent_ids:
            return
        sm = get_sessionmaker()
        window: list[DeltaMessage] = []
        window_tokens = 0
        last_created_at: str | None = None
        last_id: str | None = None
        while True:
            # Composite keyset pagination on (created_at, id) matching the
            # ORDER BY to avoid loading unbounded result sets.  UUID v4 PKs
            # have no inherent ordering, so the cursor must include
            # created_at to stay consistent with the sort. A fresh short-lived
            # session per fetch page keeps no DB connection open across the slow
            # per-window extraction the caller runs between yields.
            cursor_clause = (
                "AND (m.created_at, m.id) > "
                "(CAST(:last_created_at AS timestamptz), CAST(:last_id AS uuid)) "
                if last_id is not None
                else ""
            )
            params: dict[str, Any] = {
                "member_ids": [str(a) for a in self._member_agent_ids],
                "since": since,
                "batch_size": self._BATCH_SIZE,
            }
            if last_id is not None:
                params["last_created_at"] = last_created_at
                params["last_id"] = last_id
            # DISTINCT ON (m.created_at, m.id) collapses a message reachable via
            # several members (shared room) to one row; the ORDER BY tiebreak on
            # ca.agent_id makes the surviving row — and thus source_member_id —
            # deterministically the smallest member id. The IN list uses an
            # expanding bindparam so the member set binds as individual params
            # (same str->uuid coercion the single-agent form relied on).
            stmt = sa.text(
                "SELECT DISTINCT ON (m.created_at, m.id) "  # noqa: S608
                "m.id, m.sender_type AS role, m.content_md AS content, "
                "m.created_at, ca.agent_id AS source_member_id "
                "FROM messages m "
                "JOIN chatrooms cr ON cr.id = m.chatroom_id "
                "JOIN chatroom_agents ca ON ca.chatroom_id = cr.id "
                "WHERE ca.agent_id IN :member_ids "
                "  AND m.deleted_at IS NULL "
                "  AND (CAST(:since AS timestamptz) IS NULL OR m.created_at > :since) "
                f"{cursor_clause}"
                "ORDER BY m.created_at, m.id, ca.agent_id "
                "LIMIT :batch_size"
            ).bindparams(sa.bindparam("member_ids", expanding=True))
            async with sm() as db:
                rows = (await db.execute(stmt, params)).all()
            for r in rows:
                content = r.content or ""
                tokens = _estimate_tokens(content)
                # Flush the current window before adding a message that would
                # overflow either bound (never flush an empty window, so a single
                # oversized message still forms one window and progress is made).
                if window and (
                    len(window) >= _WINDOW_MSG_CAP or window_tokens + tokens > _WINDOW_TOKEN_BUDGET
                ):
                    yield window
                    window = []
                    window_tokens = 0
                window.append(
                    _DbMsg(id=r.id, role=r.role, content=content, source_member_id=r.source_member_id)
                )
                window_tokens += tokens
            if len(rows) < self._BATCH_SIZE:
                break
            last_row = rows[-1]
            last_created_at = str(last_row.created_at)
            last_id = str(last_row.id)
        if window:
            yield window


async def _resolve_member_agent_ids(db: AsyncSession, cfg: Any) -> list[uuid.UUID]:
    """Member agent ids whose room feeds this build ingests (R11.08).

    For an ``agent_group`` owner (the only kind Phase 2a/2b-WS1 create produces)
    this is the group's live membership — the multi-member DISTINCT union the
    loader fans across. Read live so a removed member drops out of the next
    build. Falls back to the config's derived owning agent for any other owner
    kind until WS2 adds the chatroom/workspace delta scopes.
    """
    if cfg.owner_kind == "agent_group" and cfg.owner_agent_group_id is not None:
        return list(await AgentGroupRepository(db).list_member_agent_ids(cfg.owner_agent_group_id))
    return [cfg.agent_id] if cfg.agent_id is not None else []


def _make_embedder_factory(db: AsyncSession) -> EmbedderFactory:
    """Return an EmbedderFactory that selects the key by the config's pinned
    embedding provider (Phase 2a D2), via the shared ``resolve_pinned_embed_key``
    so the build/retrieval/reconciler paths cannot drift."""
    router = build_router(db)

    async def _factory(cfg: ConfigLike) -> ResolvedEmbedder:
        provider, model, key_id = await resolve_pinned_embed_key(db, cfg)
        embedder = router_embedder_for(router=router, key_id=key_id, provider=provider, model=model)
        return ResolvedEmbedder(embedder=embedder, provider=provider, model=model)

    return _factory


_build_semaphore: asyncio.Semaphore | None = None


def _graphrag_build_concurrency() -> int:
    return max(1, get_settings().graphrag.build_concurrency)


def _get_build_semaphore() -> asyncio.Semaphore:
    """Per-worker concurrency gate for graphrag builds (D8).

    Bounds how many builds run their heavy LLM/Neo4j/Qdrant work at once so a
    burst cannot monopolise the shared worker; the cap is configurable via
    settings. Lazy + module-scoped so it binds to the worker's running loop.
    """
    global _build_semaphore
    if _build_semaphore is None:
        _build_semaphore = asyncio.Semaphore(_graphrag_build_concurrency())
    return _build_semaphore


def _reset_build_semaphore() -> None:
    """Test seam: drop the cached semaphore so a changed cap takes effect."""
    global _build_semaphore
    _build_semaphore = None


async def graphrag_build(
    ctx: dict[str, Any],
    *,
    config_id: str,
    triggered_by: str = "manual",
) -> str:
    """Run a full GraphRAG build for one config (2PC, R11.04).

    D8: bounded by a per-worker semaphore so a burst of builds cannot starve the
    other worker lanes.
    """
    async with _get_build_semaphore():
        return await _run_build(config_id=config_id, triggered_by=triggered_by)


async def _run_build(*, config_id: str, triggered_by: str = "manual") -> str:
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
    try:
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
            member_ids = await _resolve_member_agent_ids(db, cfg)
            delta_loader = _DbDeltaLoader(member_agent_ids=member_ids)

            builder = GraphRagBuilder(
                db=db,
                neo4j=neo4j,
                vector_store=vector_store,
                extractor=extractor,
                lock_store=lock_store,
                snapshot_store=snapshot_store,
                delta_loader=delta_loader,
                embedder_factory=_make_embedder_factory(db),
                configs=GraphRagConfigRepository(db),
            )
            cfg_id_str = str(cfg_id)

            # One-hot per state — set the active label to 1 and zero the others so
            # `graphrag_build_state{config_id="...", state="..."} == 1` is unique
            # at any moment. Audit M2: the prior label set used "building"/"ready"
            # which no BuildState ever maps to, so success and failed_compensating
            # both reported "idle" and operators got no "stuck compensating"
            # signal. These labels mirror the real terminal states.
            def _set_state(active: str) -> None:
                for s in ("idle", "running", "failed", "compensating"):
                    GRAPHRAG_BUILD_STATE.labels(
                        config_id=cfg_id_str,
                        state=s,
                    ).set(1.0 if s == active else 0.0)

            # Map a terminal BuildState to its metric label.
            metric_state = {
                "idle": "idle",
                "failed": "failed",
                "failed_compensating": "compensating",
            }

            _set_state("running")
            try:
                result = await builder.run(config_id=cfg_id, triggered_by=triggered_by)
                await db.commit()
                _set_state(metric_state.get(result.state.value, "idle"))
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
