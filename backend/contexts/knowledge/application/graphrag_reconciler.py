"""Reconciliation loop for GraphRAG 2PC compensation (E.7 / §11.2a / R11.04).

Behaviour:
- Runs every 60s (R11.04 — *not* 10min).
- Scans ``graphrag_configs`` for rows in ``failed_compensating``.
- For each: retries the Qdrant phase-2 up to 5 times with exponential
  backoff (1s, 2s, 4s, 8s, 16s). On success, finalises to ``idle`` and
  stamps ``last_build_at``.
- If retries are exhausted, rolls Neo4j back from the Redis snapshot and
  finalises as ``failed`` — preserving the previous active build intact.

The service does not own the clock or sleep calls directly: a
``sleeper`` and ``clock`` are injected so unit tests can drive
iterations deterministically.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_events import publish_build_state
from contexts.knowledge.application.graphrag_ports import (
    BuildLockStore,
    Neo4jDriver,
    SnapshotStore,
)
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository as GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)
from shared_kernel import audit

_log = logging.getLogger(__name__)

RETRY_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)

# Mirrors the builder's R11a.01 lock TTL — the reconciler takes the same
# per-config lock so it never heals a build that is still live in a worker.
LOCK_TTL_S = 10 * 60

# States the reconciler will attempt to heal: a Phase-2 failure
# (FAILED_COMPENSATING) and a build that durably committed Neo4j but crashed
# before Phase-2 finished (NEO4J_COMMITTED, audit C2).
_STUCK_STATES: tuple[BuildState, ...] = (
    BuildState.FAILED_COMPENSATING,
    BuildState.NEO4J_COMMITTED,
)

Sleeper = Callable[[float], Awaitable[None]]


class ReconciliationLoop:
    """Periodic scanner that heals stuck Phase-2 builds."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        neo4j: Neo4jDriver,
        vector_store: GraphRagVectorStore,
        snapshot_store: SnapshotStore,
        phase2_retry: Phase2Retry,
        sleeper: Sleeper | None = None,
        lock_store: BuildLockStore | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._neo4j = neo4j
        self._vectors = vector_store
        self._snapshots = snapshot_store
        self._phase2 = phase2_retry
        self._sleep: Sleeper = sleeper or asyncio.sleep
        self._locks = lock_store

    async def run_once(self) -> list[uuid.UUID]:
        """Drive one scan-and-heal cycle. Returns ids successfully committed.

        DOM-3: each config is committed independently. The previous code
        opened one session, reconciled every stuck config, then committed
        once at the end — so a single config raising would propagate out
        of the loop and the ``finally`` would close the session
        *uncommitted*, discarding the healed peers and their
        ``graphrag.reconciled`` audit rows. Now one config's failure only
        rolls back that config; peers stay durable.
        """
        db = self._session_factory()
        try:
            repo = GraphRagConfigRepository(db)
            stuck: list[GraphRagConfig] = []
            for state in _STUCK_STATES:
                stuck.extend(await repo.list_in_state(state))
            touched: list[uuid.UUID] = []
            for cfg in stuck:
                # Audit M1: take the per-config build lock so a still-live build
                # is never reconciled concurrently. A held lock (busy) means a
                # worker owns this build right now — skip it this cycle.
                if self._locks is not None and not await self._locks.acquire(cfg.id, ttl_s=LOCK_TTL_S):
                    continue
                try:
                    await self._reconcile_one(db, cfg)
                    await db.commit()
                    touched.append(cfg.id)
                except Exception:
                    _log.exception(
                        "graphrag reconcile failed for config %s; " "peers in this cycle are unaffected",
                        cfg.id,
                    )
                    await db.rollback()
                finally:
                    if self._locks is not None:
                        await self._locks.release(cfg.id)
            return touched
        finally:
            await db.close()

    async def run_forever(self, *, period_s: float = 60.0) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                _log.exception("graphrag reconciler iteration failed")
            await self._sleep(period_s)

    async def _reconcile_one(
        self,
        db: AsyncSession,
        cfg: GraphRagConfig,
    ) -> None:
        build_id = await self._resolve_build_id(cfg)
        if build_id is None:
            # No snapshot → nothing to compensate; mark failed outright.
            await GraphRagConfigRepository(db).set_state(
                config_id=cfg.id,
                state=BuildState.FAILED,
                error="no snapshot available for compensation",
            )
            await self._clear_current(cfg.id)
            await publish_build_state(cfg.id, BuildState.FAILED.value)
            return

        for attempt, backoff in enumerate(RETRY_BACKOFF_S, start=1):
            await self._sleep(backoff)
            try:
                await self._phase2(cfg=cfg, build_id=build_id)
            except Exception as exc:
                _log.warning(
                    "graphrag phase2 retry %d/%d failed: %s",
                    attempt,
                    len(RETRY_BACKOFF_S),
                    exc,
                )
                continue
            # Success — finalise.
            #
            # DOM-8: no superseded-entity sweep here. The builder sweeps using
            # the exact entity names it just embedded; the reconciler only
            # re-runs Phase-2 and never sees that list, and a blanket
            # build-scoped delete would wipe live entities from earlier delta
            # builds. Any duplicates this recovered build leaves behind are
            # cleared by the next normal build that re-embeds those entities.
            repo = GraphRagConfigRepository(db)
            await repo.set_state(
                config_id=cfg.id,
                state=BuildState.QDRANT_COMMITTED,
                error=None,
            )
            await repo.set_state(
                config_id=cfg.id,
                state=BuildState.IDLE,
                error=None,
                stamp_built_at=True,
            )
            await self._snapshots.delete(
                config_id=cfg.id,
                build_id=build_id,
            )
            await self._clear_current(cfg.id)
            await audit.emit(
                db,
                audit.AuditEvent(
                    action="graphrag.reconciled",
                    resource_type="graphrag_config",
                    resource_id=cfg.id,
                    metadata={
                        "build_id": str(build_id),
                        "attempt": attempt,
                        "outcome": "qdrant_recovered",
                    },
                ),
            )
            await publish_build_state(cfg.id, BuildState.IDLE.value, build_id=build_id)
            return

        # Retries exhausted → rollback Neo4j from snapshot.
        await self._rollback(db, cfg=cfg, build_id=build_id)

    async def _rollback(
        self,
        db: AsyncSession,
        *,
        cfg: GraphRagConfig,
        build_id: uuid.UUID,
    ) -> None:
        snapshot = await self._snapshots.get(
            config_id=cfg.id,
            build_id=build_id,
        )
        if snapshot is not None:
            try:
                await self._neo4j.delete_by_build(
                    config_id=cfg.id,
                    build_id=build_id,
                )
                await self._neo4j.restore_from_snapshot(
                    config_id=cfg.id,
                    snapshot=snapshot,
                )
            except Exception as exc:
                _log.exception("graphrag rollback failed: %s", exc)
        await GraphRagConfigRepository(db).set_state(
            config_id=cfg.id,
            state=BuildState.FAILED,
            error="phase2 retries exhausted; rolled back",
        )
        await publish_build_state(cfg.id, BuildState.FAILED.value, build_id=build_id)
        await self._snapshots.delete(
            config_id=cfg.id,
            build_id=build_id,
        )
        await self._clear_current(cfg.id)
        await audit.emit(
            db,
            audit.AuditEvent(
                action="graphrag.reconciled",
                resource_type="graphrag_config",
                resource_id=cfg.id,
                metadata={
                    "build_id": str(build_id),
                    "outcome": "rolled_back",
                },
            ),
        )

    async def _clear_current(self, config_id: uuid.UUID) -> None:
        """Drop the authoritative in-flight build-id pointer on a terminal heal."""
        clearer = getattr(self._snapshots, "clear_current", None)
        if clearer is not None:
            await clearer(config_id=config_id)

    async def _resolve_build_id(
        self,
        cfg: GraphRagConfig,
    ) -> uuid.UUID | None:
        """Resolve the in-flight build_id for a stuck config.

        Audit C4: prefer the authoritative ``get_current`` pointer the builder
        sets when it takes the snapshot. Fall back to the legacy (non-
        deterministic) ``scan_current`` only for adapters that predate the
        pointer. Adapters that can do neither return ``None``.
        """
        getter = getattr(self._snapshots, "get_current", None)
        if getter is not None:
            current = await getter(config_id=cfg.id)
            if current is not None:
                return current  # type: ignore[no-any-return]
        scanner = getattr(self._snapshots, "scan_current", None)
        if scanner is None:
            return None
        return await scanner(config_id=cfg.id)  # type: ignore[no-any-return]


Phase2Retry = Callable[..., Awaitable[None]]
"""Awaitable that re-runs Phase-2 (embed+upsert) using the provided cfg+build_id.

Signature: ``async def(*, cfg: GraphRagConfig, build_id: uuid.UUID) -> None``.
"""


__all__ = [
    "Phase2Retry",
    "RETRY_BACKOFF_S",
    "ReconciliationLoop",
    "Sleeper",
]
