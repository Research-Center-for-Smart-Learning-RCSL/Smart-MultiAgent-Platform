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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_config_service import (
    purge_config_external_stores,
)
from contexts.knowledge.application.graphrag_events import publish_build_state
from contexts.knowledge.application.graphrag_ports import (
    BuildLockStore,
    ConfigLike,
    GraphRagConfigRepositoryPort,
    Neo4jDriver,
    SnapshotStore,
)
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)
from shared_kernel import audit

_log = logging.getLogger(__name__)

RETRY_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)

# Mirrors the builder's R11a.01 lock TTL — the reconciler takes the same
# per-config lock so it never heals a build that is still live in a worker.
LOCK_TTL_S = 10 * 60

# States the reconciler will attempt to heal:
# - FAILED_COMPENSATING: a Phase-2 failure (retry Qdrant, else roll back).
# - NEO4J_COMMITTED: durably committed Neo4j but crashed before Phase-2 (audit C2).
# - RUNNING: crashed during Phase-1 (audit review #1) — without this, making
#   RUNNING durable (C1) would wedge the config forever, since trigger_build and
#   the builder both refuse any state outside {IDLE, FAILED}. A live build holds
#   the per-config lock, so the lock acquire in run_once skips it; only a dead
#   RUNNING (lock expired/released) is reclaimed, and it rolls straight back
#   (Phase-1 never finished, so there is no Phase-2 to retry).
_STUCK_STATES: tuple[BuildState, ...] = (
    BuildState.FAILED_COMPENSATING,
    BuildState.NEO4J_COMMITTED,
    BuildState.RUNNING,
)

Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class GraphConsumer:
    """One graph subsystem that shares the Neo4j node space (subgraphs keyed solely
    by ``config_id`` across tables — e.g. ``graphrag_configs`` and ``knowmap_configs``).

    Bundles what the orphan sweep needs to reason about a consumer as data rather than
    a special case: the config repository factory (for its live + soft-deleted ids),
    its Qdrant vector store, and the audit ``resource_type`` for its configs. A new
    consumer becomes one more list entry — no new sweep parameters."""

    repo_factory: Callable[[AsyncSession], GraphRagConfigRepositoryPort]
    vector_store: GraphRagVectorStore
    resource_type: str


class ReconciliationLoop:
    """Periodic scanner that heals stuck Phase-2 builds."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        repo_factory: Callable[[AsyncSession], GraphRagConfigRepositoryPort],
        neo4j: Neo4jDriver,
        vector_store: GraphRagVectorStore,
        snapshot_store: SnapshotStore,
        phase2_retry: Phase2Retry,
        sleeper: Sleeper | None = None,
        lock_store: BuildLockStore | None = None,
        sweep_consumers: Sequence[GraphConsumer] = (),
    ) -> None:
        self._session_factory = session_factory
        self._repo_factory = repo_factory
        self._neo4j = neo4j
        self._vectors = vector_store
        self._snapshots = snapshot_store
        self._phase2 = phase2_retry
        self._sleep: Sleeper = sleeper or asyncio.sleep
        self._locks = lock_store
        # Every graph consumer that shares this Neo4j node space (config ids live in
        # different tables — graphrag_configs, knowmap_configs). The orphan sweep
        # unions their live ids to protect each consumer's subgraphs, and attributes
        # + purges each orphan to the consumer that owns it. Empty means this loop
        # only heals its own stuck builds and runs no sweep — the second consumer's
        # loop leaves the single consumer-aware sweep to the primary loop, so two
        # sweeps never race or double the audit rows.
        self._sweep_consumers = list(sweep_consumers)

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
            repo = self._repo_factory(db)
            stuck: list[tuple[BuildState, ConfigLike]] = []
            for state in _STUCK_STATES:
                stuck.extend((state, cfg) for cfg in await repo.list_in_state(state))
            touched: list[uuid.UUID] = []
            for state, cfg in stuck:
                # Audit M1: take the per-config build lock so a still-live build
                # is never reconciled concurrently. A held lock (busy) means a
                # worker owns this build right now — skip it this cycle.
                if self._locks is not None:
                    if not await self._locks.acquire(cfg.id, ttl_s=LOCK_TTL_S):
                        continue
                elif state is BuildState.RUNNING:
                    # Without a lock store there is no way to distinguish a live
                    # RUNNING build from a dead one, and reconciling rolls the
                    # build back (destructive). Never do that blind — skip.
                    _log.warning("graphrag reconcile: no lock store; skipping RUNNING config %s", cfg.id)
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
            if self._sweep_consumers:
                await self._run_orphan_sweep(db)
            return touched
        finally:
            await db.close()

    async def _run_orphan_sweep(self, db: AsyncSession) -> None:
        """Purge graph data whose config is no longer live in Postgres (backstop).

        The inline delete cascade (DOM-4) is the primary teardown; this is the
        safety net for graph data left behind when that cascade could not run or
        did not finish: a config hard-deleted out from under its graph (e.g. a
        retention CASCADE) *or* one soft-deleted whose best-effort inline purge
        failed. An orphan is any subgraph whose config id is not in the union of
        every consumer's live (non-deleted) ids — so one consumer's live subgraphs
        are never swept as another's orphans. The purge is idempotent, so a config
        whose inline purge already succeeded has no graph data left, is absent from
        ``list_config_ids``, and is never revisited.

        Each orphan is attributed to the consumer whose table still holds its
        (soft-deleted) row so the audit records the correct ``resource_type``; a
        fully-gone config is unattributable and recorded as ``graph_config``. Its
        Neo4j subgraph is purged once and every consumer's Qdrant collection is
        swept (a no-op for non-owners) so a mis-/un-attributed orphan never leaks
        vectors. Failures are isolated per orphan and never abort the cycle.
        """
        live_ids: set[uuid.UUID] = set()
        # (consumer, its live+soft-deleted ids) — the soft-deleted set attributes an
        # orphan whose row still exists to the consumer that owns it.
        owned: list[tuple[GraphConsumer, set[uuid.UUID]]] = []
        for consumer in self._sweep_consumers:
            repo = consumer.repo_factory(db)
            live_ids |= await repo.list_all_ids()
            owned.append((consumer, await repo.list_all_ids(include_deleted=True)))
        try:
            graph_configs = await self._neo4j.list_config_ids()
        except Exception:
            _log.exception("graphrag orphan sweep: failed to enumerate graph config ids")
            return
        for config_id, project_id in graph_configs:
            if config_id in live_ids:
                continue
            owner = next((c for c, ids in owned if config_id in ids), None)
            # Purge Neo4j + one store via the shared teardown, then the remaining
            # consumers' collections. Prefer the owning consumer's store for the
            # shared step; an unattributable orphan falls back to the first consumer
            # (every collection is swept regardless, so nothing leaks).
            primary = owner or self._sweep_consumers[0]
            try:
                outcome = await purge_config_external_stores(
                    config_id=config_id,
                    project_id=project_id,
                    neo4j=self._neo4j,
                    vectors=primary.vector_store,
                )
                vectors_purged: dict[str, bool] = {}
                if project_id is not None:
                    vectors_purged[primary.resource_type] = outcome["qdrant_purged"]
                    for consumer in self._sweep_consumers:
                        if consumer is primary:
                            continue
                        try:
                            await consumer.vector_store.delete_by_config(
                                project_id=project_id, config_id=config_id
                            )
                            vectors_purged[consumer.resource_type] = True
                        except Exception:
                            vectors_purged[consumer.resource_type] = False
                            _log.exception(
                                "graphrag orphan sweep: vector purge failed for %s (%s)",
                                config_id,
                                consumer.resource_type,
                            )
                await audit.emit(
                    db,
                    audit.AuditEvent(
                        action="graphrag.orphan_swept",
                        resource_type=owner.resource_type if owner is not None else "graph_config",
                        resource_id=config_id,
                        metadata={
                            "project_id": str(project_id) if project_id else None,
                            "neo4j_purged": outcome["neo4j_purged"],
                            "vectors_purged": vectors_purged,
                        },
                    ),
                )
                await db.commit()
            except Exception:
                _log.exception("graphrag orphan sweep: purge failed for config %s", config_id)
                await db.rollback()

    async def _reconcile_one(
        self,
        db: AsyncSession,
        cfg: ConfigLike,
    ) -> None:
        build_id = await self._resolve_build_id(cfg)
        if build_id is None:
            # No snapshot → nothing to compensate; mark failed outright.
            await self._repo_factory(db).set_state(
                config_id=cfg.id,
                state=BuildState.FAILED,
                error="no snapshot available for compensation",
            )
            await self._clear_current(cfg.id)
            await publish_build_state(cfg.id, BuildState.FAILED.value)
            return

        if cfg.last_build_state is BuildState.RUNNING:
            # Audit review #1: a crashed Phase-1 build. Phase-1 never completed
            # durably (NEO4J_COMMITTED was never reached), so there is no Qdrant
            # phase to retry — roll back any partial Neo4j writes from this
            # build and fail it (re-triggerable).
            await self._rollback(db, cfg=cfg, build_id=build_id)
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
            repo = self._repo_factory(db)
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
        cfg: ConfigLike,
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
        await self._repo_factory(db).set_state(
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
        cfg: ConfigLike,
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

Signature: ``async def(*, cfg: ConfigLike, build_id: uuid.UUID) -> None``.
"""


__all__ = [
    "GraphConsumer",
    "Phase2Retry",
    "RETRY_BACKOFF_S",
    "ReconciliationLoop",
    "Sleeper",
]
