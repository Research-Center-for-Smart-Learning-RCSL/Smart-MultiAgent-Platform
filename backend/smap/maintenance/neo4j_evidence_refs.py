"""Copy Neo4j relation evidence to ``evidence_refs`` (rename release A backfill).

Release A of ``docs/tasks/2026-10-12-graphrag-phase5a-evidence-refs-expand``
writes both ``evidence_msg_ids`` and ``evidence_refs`` on every edge it touches,
but a Concept Map delta build never touches older edges, so those keep only the
old key. This command copies the old key onto every edge where the new one is
missing, one config at a time under that config's build lock, in batches. A
config whose lock is held is skipped and reported.

Run it only once release A is on every web and worker replica. Its "0 remaining"
report, from release A's build of this command, is what release B of the contract
dossier (``2026-10-12-graphrag-phase5a2-evidence-refs-contract``) is gated on.
Release A's build also re-copies an edge whose keys differ, which repairs one an
N-1 writer changed after the copy. From release B on, the new key is
authoritative, so an edge whose keys differ is left alone.

Dry-run unless ``SMAP_NEO4J_EVIDENCE_REFS_ARMED`` is truthy; see
``purge_session_dirs._ARMED_ENV`` for why that is not a flag.
"""

from __future__ import annotations

import enum
import logging
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.workers.agent_fs_gc import _TRUTHY
from contexts.knowledge.application.graphrag_builder import LOCK_TTL_S
from contexts.knowledge.application.graphrag_ports import BuildLockStore

_log = logging.getLogger(__name__)

ARMED_ENV = "SMAP_NEO4J_EVIDENCE_REFS_ARMED"
_BATCH = 1000


class _Driver(Protocol):
    async def count_unmigrated_evidence(self) -> dict[str, int]: ...

    async def copy_evidence_refs(self, *, config_id: uuid.UUID, batch: int) -> int: ...


class _Outcome(enum.Enum):
    DONE = "done"
    BUSY = "busy"
    LOST_LOCK = "lost_lock"


@dataclass(frozen=True, slots=True)
class EvidenceRefsReport:
    dry_run: bool
    pending: int
    copied: int
    remaining: int
    # Never acquired: a build held the lock.
    busy: tuple[uuid.UUID, ...] = ()
    # Acquired, then lost mid-copy: a build may have written alongside the copy.
    lost_lock: tuple[uuid.UUID, ...] = ()
    # The copy raised; the run went on with the other configs.
    failed: tuple[uuid.UUID, ...] = ()
    # Edges whose config id is missing or not a UUID: no lock can cover them, so
    # they are counted in ``remaining`` and left alone.
    unattributed: int = 0


def is_armed() -> bool:
    return os.environ.get(ARMED_ENV, "").strip().lower() in _TRUTHY


async def run(
    *,
    driver: _Driver | None = None,
    lock_store: BuildLockStore | None = None,
    config_ids: Iterable[uuid.UUID] | None = None,
    batch: int = _BATCH,
) -> EvidenceRefsReport:
    """Count, and when armed copy, unmigrated edges; ``config_ids`` narrows the scope."""
    from app.config.settings import get_settings
    from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
    from contexts.knowledge.infrastructure.redis_lock import RedisBuildLockStore
    from shared_kernel.auth.clients import close_redis

    scope = set(config_ids) if config_ids is not None else None
    own_driver: Neo4jAsyncDriver | None = None
    if driver is None:
        s = get_settings()
        own_driver = Neo4jAsyncDriver(uri=s.neo4j.url, auth=(s.neo4j.user, s.neo4j.password))
        driver = own_driver
    locks: BuildLockStore = lock_store if lock_store is not None else RedisBuildLockStore()
    try:
        return await _run(driver, locks, scope, batch)
    finally:
        if own_driver is not None:
            await own_driver.close()
        if lock_store is None:
            # The default store uses the process-wide client; close it before
            # asyncio.run closes the loop its connections are bound to.
            await close_redis()


def _split(raw: dict[str, int], scope: set[uuid.UUID] | None) -> tuple[dict[uuid.UUID, int], int]:
    """Per-config counts in scope, and the count of edges no config id covers."""
    by_config: dict[uuid.UUID, int] = {}
    unattributed = 0
    for key, n in raw.items():
        try:
            cid = uuid.UUID(key)
        except ValueError:
            unattributed += n
            continue
        if n and (scope is None or cid in scope):
            by_config[cid] = n
    # A scoped run is about named configs; edges outside any config are not theirs.
    return by_config, (0 if scope is not None else unattributed)


async def _run(
    driver: _Driver, locks: BuildLockStore, scope: set[uuid.UUID] | None, batch: int
) -> EvidenceRefsReport:
    before, unattributed = _split(await driver.count_unmigrated_evidence(), scope)
    pending = sum(before.values()) + unattributed
    if unattributed:
        _log.warning(
            "neo4j-evidence-refs: %d edge(s) have no usable config id and are not copied", unattributed
        )
    if not is_armed():
        return EvidenceRefsReport(
            dry_run=True, pending=pending, copied=0, remaining=pending, unattributed=unattributed
        )
    copied = 0
    outcomes: dict[_Outcome, list[uuid.UUID]] = {o: [] for o in _Outcome}
    failed: list[uuid.UUID] = []
    for cid in sorted(before, key=str):
        try:
            done, outcome = await _copy_config(driver, locks, cid, batch)
        except Exception:
            # One config's store error must not cost the others their run or the
            # operator the report.
            _log.exception("neo4j-evidence-refs: copy failed for config %s", cid)
            failed.append(cid)
            continue
        copied += done
        outcomes[outcome].append(cid)
    after, unattributed_after = _split(await driver.count_unmigrated_evidence(), scope)
    return EvidenceRefsReport(
        dry_run=False,
        pending=pending,
        copied=copied,
        remaining=sum(after.values()) + unattributed_after,
        busy=tuple(outcomes[_Outcome.BUSY]),
        lost_lock=tuple(outcomes[_Outcome.LOST_LOCK]),
        failed=tuple(failed),
        unattributed=unattributed_after,
    )


async def _copy_config(
    driver: _Driver, locks: BuildLockStore, cid: uuid.UUID, batch: int
) -> tuple[int, _Outcome]:
    """Copy one config's edges under its build lock."""
    if not await locks.acquire(cid, ttl_s=LOCK_TTL_S):
        _log.warning("neo4j-evidence-refs: config %s is building; skipped", cid)
        return 0, _Outcome.BUSY
    copied = 0
    try:
        while True:
            n = await driver.copy_evidence_refs(config_id=cid, batch=batch)
            copied += n
            if n == 0:
                return copied, _Outcome.DONE
            # Fail closed like the builder: without the lock a build may be
            # writing these edges, so stop and let a re-run finish the config.
            if not await locks.refresh(cid, ttl_s=LOCK_TTL_S):
                _log.warning("neo4j-evidence-refs: lost the lock for config %s; stopped", cid)
                return copied, _Outcome.LOST_LOCK
    finally:
        await locks.release(cid)


__all__ = ["ARMED_ENV", "EvidenceRefsReport", "is_armed", "run"]
