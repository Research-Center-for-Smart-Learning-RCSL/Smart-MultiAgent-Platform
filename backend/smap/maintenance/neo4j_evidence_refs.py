"""Remove the old Neo4j relation evidence key (the rename's clean-up).

Release C of ``docs/tasks/2026-10-12-graphrag-phase5a2-evidence-refs-contract``
reads and writes only ``evidence_refs`` and drops ``evidence_msg_ids`` from every
edge it writes, but a Concept Map delta build never touches older edges, so those
keep both keys from release B. This command removes the old key from every edge
that still has it, keeping the new key (filling it from the old one if an edge
somehow lacks it), one config at a time under that config's build lock, in
batches. A config whose lock is held is skipped and reported.

Run it only once the clean-up gate in that dossier's Q-2 is met: release C on
every replica that reads or writes evidence, so no writer sets the old key again,
and 24 h since release B rolled out. Idempotent. It replaced the release A copy mode, which copied
the old key over a differing new one and is wrong once the new key is
authoritative (D-2 in that dossier). Dry-run unless
``SMAP_NEO4J_EVIDENCE_REFS_ARMED`` is truthy; see ``purge_session_dirs._ARMED_ENV``
for why that is not a flag.
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
    async def count_legacy_evidence(self) -> dict[str, int]: ...

    async def remove_legacy_evidence(self, *, config_id: uuid.UUID, batch: int) -> int: ...


class _Outcome(enum.Enum):
    DONE = "done"
    BUSY = "busy"
    LOST_LOCK = "lost_lock"


@dataclass(frozen=True, slots=True)
class EvidenceRefsReport:
    dry_run: bool
    pending: int
    removed: int
    remaining: int
    # Never acquired: a build held the lock.
    busy: tuple[uuid.UUID, ...] = ()
    # Acquired, then lost mid-run: a build may have written alongside.
    lost_lock: tuple[uuid.UUID, ...] = ()
    # The removal raised; the run went on with the other configs.
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
    """Count, and when armed strip, legacy edges; ``config_ids`` narrows the scope."""
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
    before, unattributed = _split(await driver.count_legacy_evidence(), scope)
    pending = sum(before.values()) + unattributed
    if unattributed:
        _log.warning(
            "neo4j-evidence-refs: %d edge(s) have no usable config id and are left alone", unattributed
        )
    if not is_armed():
        return EvidenceRefsReport(
            dry_run=True, pending=pending, removed=0, remaining=pending, unattributed=unattributed
        )
    removed = 0
    outcomes: dict[_Outcome, list[uuid.UUID]] = {o: [] for o in _Outcome}
    failed: list[uuid.UUID] = []
    for cid in sorted(before, key=str):
        try:
            done, outcome = await _strip_config(driver, locks, cid, batch)
        except Exception:
            # One config's store error must not cost the others their run or the
            # operator the report.
            _log.exception("neo4j-evidence-refs: removal failed for config %s", cid)
            failed.append(cid)
            continue
        removed += done
        outcomes[outcome].append(cid)
    after, unattributed_after = _split(await driver.count_legacy_evidence(), scope)
    return EvidenceRefsReport(
        dry_run=False,
        pending=pending,
        removed=removed,
        remaining=sum(after.values()) + unattributed_after,
        busy=tuple(outcomes[_Outcome.BUSY]),
        lost_lock=tuple(outcomes[_Outcome.LOST_LOCK]),
        failed=tuple(failed),
        unattributed=unattributed_after,
    )


async def _strip_config(
    driver: _Driver, locks: BuildLockStore, cid: uuid.UUID, batch: int
) -> tuple[int, _Outcome]:
    """Strip one config's edges under its build lock."""
    if not await locks.acquire(cid, ttl_s=LOCK_TTL_S):
        _log.warning("neo4j-evidence-refs: config %s is building; skipped", cid)
        return 0, _Outcome.BUSY
    removed = 0
    try:
        while True:
            n = await driver.remove_legacy_evidence(config_id=cid, batch=batch)
            removed += n
            if n == 0:
                return removed, _Outcome.DONE
            # Fail closed like the builder: without the lock a build may be
            # writing these edges, so stop and let a re-run finish the config.
            if not await locks.refresh(cid, ttl_s=LOCK_TTL_S):
                _log.warning("neo4j-evidence-refs: lost the lock for config %s; stopped", cid)
                return removed, _Outcome.LOST_LOCK
    finally:
        await locks.release(cid)


__all__ = ["ARMED_ENV", "EvidenceRefsReport", "is_armed", "run"]
