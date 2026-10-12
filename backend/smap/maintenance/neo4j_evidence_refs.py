"""Copy Neo4j relation evidence to ``evidence_refs`` (rename release A backfill).

Release A of ``docs/tasks/2026-10-12-graphrag-phase5a-evidence-refs-expand``
writes both ``evidence_msg_ids`` and ``evidence_refs`` on every edge it touches,
but a Concept Map delta build never touches older edges, so those keep only the
old key. This command copies the old key onto every edge where the new one is
missing or differs, one config at a time under that config's build lock, in
batches. A config whose lock is held is skipped and reported.

Run it only once release A is on every web and worker replica: before that, an
N-1 writer can change the old key after the copy. Re-running repairs such an
edge, since the command is idempotent. Its "0 remaining" report is what the
contract dossier (``2026-10-12-graphrag-phase5a2-evidence-refs-contract``) is
gated on. Dry-run unless ``SMAP_NEO4J_EVIDENCE_REFS_ARMED`` is truthy; see
``purge_session_dirs._ARMED_ENV`` for why that is not a flag.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.workers.agent_fs_gc import _TRUTHY
from contexts.knowledge.application.graphrag_builder import LOCK_TTL_S

_log = logging.getLogger(__name__)

ARMED_ENV = "SMAP_NEO4J_EVIDENCE_REFS_ARMED"
_BATCH = 1000


class _Driver(Protocol):
    async def count_unmigrated_evidence(self) -> dict[uuid.UUID, int]: ...

    async def copy_evidence_refs(self, *, config_id: uuid.UUID, batch: int) -> int: ...


class _Locks(Protocol):
    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool: ...

    async def refresh(self, config_id: uuid.UUID, *, ttl_s: int) -> bool: ...

    async def release(self, config_id: uuid.UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class EvidenceRefsReport:
    dry_run: bool
    pending: int
    copied: int
    remaining: int
    busy: tuple[uuid.UUID, ...]


def is_armed() -> bool:
    return os.environ.get(ARMED_ENV, "").strip().lower() in _TRUTHY


async def run(
    *,
    driver: _Driver | None = None,
    lock_store: _Locks | None = None,
    config_ids: Iterable[uuid.UUID] | None = None,
    batch: int = _BATCH,
) -> EvidenceRefsReport:
    """Count, and when armed copy, unmigrated edges; ``config_ids`` narrows the scope."""
    if lock_store is None:
        from contexts.knowledge.infrastructure.redis_lock import RedisBuildLockStore

        lock_store = RedisBuildLockStore()
    scope = set(config_ids) if config_ids is not None else None
    if driver is not None:
        return await _run(driver, lock_store, scope, batch)

    from app.config.settings import get_settings
    from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

    s = get_settings()
    own = Neo4jAsyncDriver(uri=s.neo4j.url, auth=(s.neo4j.user, s.neo4j.password))
    try:
        return await _run(own, lock_store, scope, batch)
    finally:
        await own.close()


async def _run(
    driver: _Driver, locks: _Locks, scope: set[uuid.UUID] | None, batch: int
) -> EvidenceRefsReport:
    before = _in_scope(await driver.count_unmigrated_evidence(), scope)
    pending = sum(before.values())
    if not is_armed():
        return EvidenceRefsReport(dry_run=True, pending=pending, copied=0, remaining=pending, busy=())
    copied = 0
    busy: list[uuid.UUID] = []
    for cid in sorted(before, key=str):
        done, held = await _copy_config(driver, locks, cid, batch)
        copied += done
        if held:
            busy.append(cid)
    remaining = sum(_in_scope(await driver.count_unmigrated_evidence(), scope).values())
    return EvidenceRefsReport(
        dry_run=False, pending=pending, copied=copied, remaining=remaining, busy=tuple(busy)
    )


def _in_scope(counts: dict[uuid.UUID, int], scope: set[uuid.UUID] | None) -> dict[uuid.UUID, int]:
    return {cid: n for cid, n in counts.items() if n and (scope is None or cid in scope)}


async def _copy_config(driver: _Driver, locks: _Locks, cid: uuid.UUID, batch: int) -> tuple[int, bool]:
    """Copy one config's edges under its build lock; returns (copied, skipped as busy)."""
    if not await locks.acquire(cid, ttl_s=LOCK_TTL_S):
        _log.warning("neo4j-evidence-refs: config %s is building; skipped", cid)
        return 0, True
    copied = 0
    try:
        while True:
            n = await driver.copy_evidence_refs(config_id=cid, batch=batch)
            copied += n
            if n == 0:
                return copied, False
            # Fail closed like the builder: without the lock a build may be
            # writing these edges, so stop and let a re-run finish the config.
            if not await locks.refresh(cid, ttl_s=LOCK_TTL_S):
                _log.warning("neo4j-evidence-refs: lost the lock for config %s; stopped", cid)
                return copied, True
    finally:
        await locks.release(cid)


__all__ = ["ARMED_ENV", "EvidenceRefsReport", "is_armed", "run"]
