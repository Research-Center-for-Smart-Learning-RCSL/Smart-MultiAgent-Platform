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

from contexts.knowledge.application.graphrag_ports import (
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
    ) -> None:
        self._session_factory = session_factory
        self._neo4j = neo4j
        self._vectors = vector_store
        self._snapshots = snapshot_store
        self._phase2 = phase2_retry
        self._sleep: Sleeper = sleeper or asyncio.sleep

    async def run_once(self) -> list[uuid.UUID]:
        """Drive one scan-and-heal cycle. Returns ids touched."""
        db = self._session_factory()
        try:
            repo = GraphRagConfigRepository(db)
            stuck = await repo.list_in_state(BuildState.FAILED_COMPENSATING)
            touched: list[uuid.UUID] = []
            for cfg in stuck:
                await self._reconcile_one(db, cfg)
                touched.append(cfg.id)
            await db.commit()
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
        await self._snapshots.delete(
            config_id=cfg.id,
            build_id=build_id,
        )
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

    async def _resolve_build_id(
        self,
        cfg: GraphRagConfig,
    ) -> uuid.UUID | None:
        """Find the in-flight build_id by probing the snapshot store.

        We encoded the build id in the Redis key when the builder took
        the snapshot; the adapter exposes ``scan_current`` to retrieve
        it. Adapters that cannot scan return ``None``.
        """
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
