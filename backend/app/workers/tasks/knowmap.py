"""Arq tasks for the Knowledge Map (Phase 3).

- ``knowmap_ingest_document`` — off-request parse/chunk/persist for a tus-registered
  document (large files must not chunk synchronously inside the final PATCH), then
  enqueues the graph build so the committed corpus change is reflected.
- ``knowmap_scan_document`` — ClamAV malware scan flipping ``scan_status``; a
  quarantine verdict triggers a rebuild so the graph drops the tainted document.
- ``knowmap_revision_sweep`` — minute cron reconciling committed corpus revisions
  that the best-effort finalize/enqueue path never turned into a queued build.

Mirrors ``app/workers/tasks/rag.py`` over the ``knowmap_*`` repositories.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession

from app.wiring.knowledge_ingest import KnowledgeIngestWiring
from contexts.keys.infrastructure.adapters import build_router
from contexts.knowledge.application.embed_resolution import resolve_pinned_embed_key
from contexts.knowledge.application.graphrag_builder import (
    LOCK_TTL_S,
    EmbedderFactory,
    GraphRagBuilder,
    ResolvedEmbedder,
)
from contexts.knowledge.application.graphrag_ports import ConfigLike
from contexts.knowledge.application.knowmap_config_service import build_knowmap_embedder
from contexts.knowledge.application.knowmap_triggers import (
    EnqueueOutcome,
    enqueue_knowmap_build,
)
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.models import DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.embedders import router_embedder_for
from contexts.knowledge.infrastructure.knowmap_delta_loader import DocDeltaLoader
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from contexts.knowledge.infrastructure.knowmap_triple_extractor import DocTripleExtractor
from shared_kernel.auth.clients import now
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.queue import enqueue
from shared_kernel.queue_names import KNOWLEDGE_INGEST_QUEUE

_log = logging.getLogger(__name__)

# The build lock (LOCK_TTL_S), refreshed per window, is the single-writer guard;
# the job timeout is only a runaway backstop and must have headroom over the TTL.
KNOWMAP_BUILD_TIMEOUT_S = LOCK_TTL_S * 3

# F-27: the scan task's arq retry budget. Named so the ClamAV-error SKIPPED path
# can enqueue a rebuild only once retries are exhausted (a document mid-retry might
# still come back CLEAN), and so the `.max_tries` assignment stays in sync.
_SCAN_MAX_TRIES = 3


# F-4: deliberately tighter than the 500-per-page used by cheaper sweeps
# (``graphrag_silence_sweep``). Every config this sweep enqueues drives Neo4j,
# Qdrant and LLM extraction, so a large backlog must drain over several ticks
# instead of arriving as one herd.
_SWEEP_PAGE_SIZE = 50
_SWEEP_MAX_PER_TICK = 200

# F-4: a config still RUNNING past this had every legitimate chance to settle --
# the build's own timeout (KNOWMAP_BUILD_TIMEOUT_S), plus the reconciler's
# worst-case recovery latency (the residual LOCK_TTL_S before the lock frees, then
# one cron minute). Past it the reconciler itself is not working, which is the only
# thing this threshold is meant to surface. Generous on purpose: the check is a net
# for a broken recovery path, so a false silence costs less than a false alarm.
_STALE_RUNNING_AFTER_S = 60 * 60

# F-4 FU-3: where the next tick resumes. Without it every tick restarts at the
# lowest config ids, so a tenant holding more than a capped tick's worth of
# persistently divergent configs pins the window and configs sorting above it are
# never reached. Best-effort state: losing the key costs one non-rotating tick.
_SWEEP_CURSOR_KEY = "knowmap:sweep:cursor"

# F-4 FU-5: how long one (config, revision) may keep being re-offered before the
# sweep stops and asks for a human. Comfortably past ``keep_result`` (one hour),
# so the legitimate slow case -- a build that finished but failed to stamp its
# revision, whose re-offers dedup onto the retained result until that lapses --
# resolves long before this trips. Past it, re-offering is not recovering
# anything and every cycle is another full-corpus rebuild on the tenant's key.
_REOFFER_GIVE_UP_S = 6 * 60 * 60
_REOFFER_KEY_TTL_S = 7 * 24 * 60 * 60


async def _read_cursor(pool: Any) -> uuid.UUID | None:
    """Where the last tick stopped, or ``None`` to start from the beginning."""
    if pool is None:
        return None
    try:
        raw = await pool.get(_SWEEP_CURSOR_KEY)
    except Exception:
        _log.warning("knowmap revision sweep: cursor read failed", exc_info=True)
        return None
    if not raw:
        return None
    try:
        return uuid.UUID(raw.decode() if isinstance(raw, bytes) else str(raw))
    except ValueError:
        # A corrupt cursor must not wedge the sweep; restart from the beginning.
        return None


async def _write_cursor(pool: Any, after_id: uuid.UUID | None) -> None:
    """Persist the resume point, or clear it once a pass reaches the end."""
    if pool is None:
        return
    try:
        if after_id is None:
            await pool.delete(_SWEEP_CURSOR_KEY)
        else:
            await pool.set(_SWEEP_CURSOR_KEY, str(after_id))
    except Exception:
        _log.warning("knowmap revision sweep: cursor write failed", exc_info=True)


def _reoffer_key(config_id: uuid.UUID, revision: int) -> str:
    return f"knowmap:sweep:firstoffer:{config_id}:{revision}"


def _parse_epoch(raw: Any) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.decode() if isinstance(raw, bytes) else raw)
    except (ValueError, AttributeError):
        # A corrupt stamp must not abandon a config that may still be recoverable.
        return None


async def _partition_by_reoffer(
    pool: Any, pending: list[tuple[uuid.UUID, uuid.UUID, int]]
) -> tuple[list[tuple[uuid.UUID, int]], int]:
    """Split the work list into what to offer and what to give up on (F-4 FU-5).

    Divergence that survives repeated offers is not being recovered by them, and
    each cycle past ``keep_result`` costs the tenant another full-corpus rebuild.

    The stamps are read in one ``MGET`` rather than a round trip per config: a
    full tick is ``_SWEEP_MAX_PER_TICK`` configs and this runs every minute. Only
    first-time stamps are written, which is rare per tick since a revision is
    stamped once and never revisited.

    Fails open on any Redis error: dropping recovery because the bookkeeping
    store blinked would be the worse failure.
    """
    work = [(config_id, revision) for config_id, _project_id, revision in pending]
    if pool is None or not work:
        return work, 0
    keys = [_reoffer_key(config_id, revision) for config_id, revision in work]
    try:
        stamps = await pool.mget(keys)
    except Exception:
        _log.warning("knowmap revision sweep: re-offer bookkeeping read failed", exc_info=True)
        return work, 0

    now_epoch = int(now().timestamp())
    offerable: list[tuple[uuid.UUID, int]] = []
    unstamped: list[str] = []
    abandoned = 0
    for (config_id, revision), key, raw in zip(work, keys, stamps, strict=True):
        first = _parse_epoch(raw)
        if first is None:
            unstamped.append(key)
            offerable.append((config_id, revision))
            continue
        age = now_epoch - first
        if age < _REOFFER_GIVE_UP_S:
            offerable.append((config_id, revision))
            continue
        abandoned += 1
        _log.warning(
            "knowmap revision sweep: config %s revision %d has been re-offered for %ds "
            "without converging; giving up on it and leaving it for a human",
            config_id,
            revision,
            age,
        )

    for key in unstamped:
        try:
            await pool.set(key, str(now_epoch), ex=_REOFFER_KEY_TTL_S)
        except Exception:
            # A missed stamp only restarts this config's give-up window.
            _log.warning("knowmap revision sweep: re-offer stamp failed", exc_info=True)
            break
    return offerable, abandoned


async def _report_stale_running(configs: KnowmapConfigRepository) -> int:
    """Warn about builds the reconciler should have reclaimed and has not (F-4).

    Never enqueues: a RUNNING config is rejected by the builder's state whitelist
    anyway, so offering it work would produce noise, not recovery. Failures are
    contained by the caller — an observability probe must never be able to erase
    the report of the enqueues the tick already made.
    """
    started_before = now() - timedelta(seconds=_STALE_RUNNING_AFTER_S)
    # One bounded page is enough signal: a broken reconciler is a single incident
    # however many configs it stranded. Read one row past the page so a full page
    # can be reported as a floor (FU-7) -- a capped count is not a measurement.
    stale = await configs.list_stale_running(started_before=started_before, limit=_SWEEP_PAGE_SIZE + 1)
    capped = len(stale) > _SWEEP_PAGE_SIZE
    stale = stale[:_SWEEP_PAGE_SIZE]
    if stale:
        # One line with a sample, not one line per config: a broken reconciler is a
        # single incident, and this repeats every tick until someone fixes it.
        _log.warning(
            "knowmap revision sweep: %s%d config(s) stuck in RUNNING past the %ds threshold; "
            "the reconciler should have reclaimed them. Oldest: %s (since %s)",
            "at least " if capped else "",
            len(stale),
            _STALE_RUNNING_AFTER_S,
            stale[0].id,
            stale[0].build_started_at,
        )
    return len(stale)


async def _collect_divergent(
    db: AsyncSession, configs: KnowmapConfigRepository, after_id: uuid.UUID | None
) -> tuple[list[tuple[uuid.UUID, uuid.UUID, int]], uuid.UUID | None, bool]:
    """Page the divergence query from ``after_id`` up to the per-tick cap (F-4).

    Returns ``(config_id, project_id, target_revision)`` triples, where the next
    tick should resume, and whether a read failed part-way.

    The resume point is the last id read when the cap cut the pass short, or
    ``None`` when the pass reached the end and the next one should wrap to the
    beginning. Wrapping is what stops a large tenant low in the id order from
    pinning the window forever (FU-3).

    A read failure keeps what was gathered rather than discarding it -- half a
    tick of recovery beats none -- and reports itself so the caller leaves the
    stored cursor alone. Clearing it there would restart every subsequent tick at
    the beginning, quietly undoing the rotation FU-3 exists to provide.
    """
    pending: list[tuple[uuid.UUID, uuid.UUID, int]] = []
    while len(pending) < _SWEEP_MAX_PER_TICK:
        page_limit = min(_SWEEP_PAGE_SIZE, _SWEEP_MAX_PER_TICK - len(pending))
        try:
            page = await configs.list_revision_divergent(limit=page_limit, after_id=after_id)
        except Exception:
            _log.warning("knowmap revision sweep: divergence page read failed", exc_info=True)
            # Clear the aborted transaction, or every later read on this session
            # fails too and the tick loses its stale-build alarm as collateral.
            await db.rollback()
            return pending, None, True
        pending.extend((cfg.id, cfg.project_id, cfg.corpus_revision) for cfg in page)
        if len(page) < page_limit:
            return pending, None, False
        after_id = page[-1].id
    return pending, after_id, False


async def _drop_dead_projects(
    db: AsyncSession, pending: list[tuple[uuid.UUID, uuid.UUID, int]]
) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """Keep only configs whose project is still live (F-4 security review).

    ``ProjectService.soft_delete`` now cascades ``deleted_at`` onto these configs,
    so the divergence query's own ``deleted_at IS NULL`` filter usually settles
    this. This stays as the independent check for the cases that cascade cannot
    cover: projects deleted before migration ``0060`` backfilled them on a
    deployment that has not run it yet, and any future deletion path that forgets
    to cascade. The cost of being wrong is asymmetric -- every other build trigger
    is request-scoped behind a membership check that a deleted project already
    fails, while this cron has no request behind it, so a miss here re-reads a
    deleted project's documents, spends the tenant's provider key on extraction,
    and rewrites a graph a customer asked to be rid of.

    Checking projects is sufficient: deleting an org soft-deletes each of its
    projects in turn (``OrgService.soft_delete``). Done through the tenancy facade
    rather than a join, since these repositories hold no cross-context joins.
    """
    if not pending:
        return pending
    from contexts.tenancy.interfaces.facade import TenancyFacade

    project_ids = {project_id for _, project_id, _ in pending}
    live = set(await TenancyFacade(db).get_projects(list(project_ids)))
    dropped = len(project_ids - live)
    if dropped:
        _log.info(
            "knowmap revision sweep: skipped configs in %d deleted project(s)",
            dropped,
        )
    return [entry for entry in pending if entry[1] in live]


async def knowmap_revision_sweep(ctx: dict[str, Any]) -> str:
    """Enqueue builds for committed corpus revisions that were never queued (F-4).

    The level-triggered backstop for edge-triggered finalization. Both
    ``_finalize_build_revision`` (swallowed by ``knowmap_build`` so a bookkeeping
    error cannot make arq re-run a committed build) and ``enqueue_knowmap_build``
    are best-effort, so a durable ``corpus_revision`` can end up with no queued
    build and no further trigger to produce one. This reads that gap directly.

    Repeated ticks are idempotent: the revision-keyed job id means re-offering the
    same gap collapses onto the job already queued or running.

    Recovery latency depends on which hop dropped, and the two are worth keeping
    apart. If the *follow-up enqueue* was lost, the target revision was never
    built and its job id has never been used, so the next tick queues it within a
    minute. If instead a build finished and only the ``built_corpus_revision``
    stamp failed, the graph already holds that revision -- the divergence is
    stale bookkeeping, not missing content -- and the re-offer collapses onto the
    completed job's retained result until ``keep_result`` lapses. That shows up
    as ``deduped`` in the tick summary rather than silence.
    """
    # The worker already holds an ArqRedis; the shared enqueue helper would open
    # and close one pool per config, up to _SWEEP_MAX_PER_TICK of them per tick.
    pool = ctx.get("redis")
    sm = get_sessionmaker()
    stale_running = 0
    # Read everything first, then enqueue: shared_kernel.queue.enqueue opens and
    # closes a Redis pool per call, and holding a Postgres transaction open across
    # up to _SWEEP_MAX_PER_TICK of those round trips would pin a connection
    # idle-in-transaction for no reason.
    resume_from = await _read_cursor(pool)
    liveness_proven = True
    async with sm() as db:
        configs = KnowmapConfigRepository(db)
        pending, next_cursor, read_failed = await _collect_divergent(db, configs, resume_from)
        try:
            pending = await _drop_dead_projects(db, pending)
        except Exception:
            # Fail closed: liveness is unproven, and re-materializing a deleted
            # project's graph is worse than deferring recovery by one tick.
            _log.warning("knowmap revision sweep: project liveness check failed", exc_info=True)
            await db.rollback()
            pending = []
            liveness_proven = False
        try:
            stale_running = await _report_stale_running(configs)
        except Exception:
            # Observability only: it must never erase the enqueues below.
            _log.warning("knowmap revision sweep: stale-RUNNING report failed", exc_info=True)

    offerable, abandoned = await _partition_by_reoffer(pool, pending)

    counts = dict.fromkeys(EnqueueOutcome, 0)
    for config_id, target_revision in offerable:
        outcome = await enqueue_knowmap_build(config_id=config_id, target_revision=target_revision, pool=pool)
        counts[outcome] = counts.get(outcome, 0) + 1

    # The cursor may only move over configs this tick actually accounted for.
    # A partial page read, an unproven liveness check, or a wholesale enqueue
    # failure all mean the work was identified but never offered, and advancing
    # past it would hide those configs until the cursor wrapped the entire table.
    every_offer_failed = bool(offerable) and counts[EnqueueOutcome.FAILED] == len(offerable)
    if read_failed or not liveness_proven or every_offer_failed:
        _log.info(
            "knowmap revision sweep: holding the cursor at %s; this tick did not offer "
            "the %d config(s) it read",
            resume_from,
            len(pending),
        )
    else:
        await _write_cursor(pool, next_cursor)
        if next_cursor is not None:
            # A capped tick must not read as full coverage in the logs.
            _log.info(
                "knowmap revision sweep: capped at %d configs; the next tick resumes from %s",
                len(pending),
                next_cursor,
            )
    summary = (
        f"enqueued={counts[EnqueueOutcome.QUEUED]} "
        f"deduped={counts[EnqueueOutcome.DEDUPED]} "
        f"failed={counts[EnqueueOutcome.FAILED]} "
        f"abandoned={abandoned} "
        f"stale_running={stale_running}"
    )
    _log.info("knowmap revision sweep: %s", summary)
    return summary


async def _bump_and_enqueue_build(sm: Any, knowmap_config_id: uuid.UUID) -> None:
    """Advance the corpus revision for a scan-verdict event that changes the
    buildable corpus, then enqueue a build for the new revision (F-12 W1).

    A scan verdict flipping a document into ``ready∧clean`` (or a QUARANTINED /
    SKIPPED verdict that must exclude it) changes what the next build processes,
    but — unlike an add/reprocess/delete — does not itself go through the
    document-mutation bump. Without advancing the revision here, two documents
    uploaded together whose scans clear against the same pre-scan revision compute
    the same ``knowmap:build:{config}:{revision}`` id, and arq drops the second as
    a duplicate, silently leaving it unbuilt — the exact race F-12 closes for the
    add path, reopened for the scan-verdict edge. Bumping gives each such event a
    distinct target so it is never suppressed. ``bump_corpus_revision`` returns 0
    only when the config was concurrently deleted — nothing to build."""
    async with sm() as db, db.begin():
        new_rev = await KnowmapConfigRepository(db).bump_corpus_revision(knowmap_config_id)
    if new_rev == 0:
        return
    await enqueue_knowmap_build(config_id=knowmap_config_id, target_revision=new_rev)


async def _enqueue_rebuild_for_config(sm: Any, knowmap_config_id: uuid.UUID) -> None:
    """F-27: enqueue a full-corpus rebuild after a SKIPPED verdict, mirroring the
    QUARANTINED path, so the un-scannable document is removed from the buildable
    corpus. Under F-6's replacement semantics the rebuild also evicts the skipped
    document's triples from Neo4j. Advances the corpus revision (F-12 W1) so the
    removal build is never dropped as a duplicate of a retained prior build."""
    await _bump_and_enqueue_build(sm, knowmap_config_id)


async def _enqueue_build_on_clean(sm: Any, doc_id: uuid.UUID, *, entered: bool) -> None:
    """F-5: enqueue the graph build for a document whose scan just returned CLEAN,
    but only once it is READY.

    ``entered`` is True only when this verdict newly adds the document to the
    ready∧clean set (its prior scan status was not CLEAN). A CLEAN->CLEAN reconfirm
    (a reindex rescan) leaves membership unchanged: any content change was already
    enqueued by the ingest side's immediate clean-build, and advancing the revision
    here would double it (F-12 W3), so this returns without touching the queue.

    The mirror of the indexing side's clean-gate: if the document is not yet READY
    (async tus path, indexing still running), the index worker enqueues the build
    when it observes the clean verdict — last writer wins, so exactly one build is
    queued and a document is never left unbuilt. Re-reads the document fresh so it
    observes the indexing side's committed ``READY`` state, then advances the
    corpus revision (F-12 W1) so this clean-set entry gets a build id distinct from
    a sibling upload's.
    """
    if not entered:
        return
    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
        if doc is None or doc.status is not DocumentStatus.READY:
            return
        cfg_id = doc.knowmap_config_id
    await _bump_and_enqueue_build(sm, cfg_id)


async def knowmap_ingest_document(
    ctx: dict[str, Any],
    *,
    document_id: str,
    ingest_attempt: int | None = None,
    claim_token: str | None = None,
) -> str:
    """Index one registered Knowledge Map document, then enqueue the build.

    Idempotent: re-runs on a document no longer in ``ingesting`` state are a no-op
    (``process_document`` guards)."""
    _ = ctx
    from datetime import UTC, datetime

    from app.config.settings import get_settings

    doc_id = uuid.UUID(document_id)
    settings = get_settings()
    sm = get_sessionmaker()

    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
        if doc is None:
            _log.warning("knowmap_ingest_document: document %s not found", document_id)
            return f"document {document_id} not found"
        from contexts.knowledge.domain.models import IngestClaim

        claim = (
            IngestClaim(
                attempt=ingest_attempt,
                token=uuid.UUID(claim_token),
                until=doc.ingest_claim_until or datetime.max.replace(tzinfo=UTC),
            )
            if ingest_attempt is not None and claim_token is not None
            else None
        )
        if (ingest_attempt is None) is not (claim_token is None):
            _log.warning("knowmap_ingest_document: incomplete claim for %s", document_id)
            return "invalid ingest claim"
        if doc.scan_status is not ScanStatus.CLEAN:
            return f"scan_{doc.scan_status.value}"
        cfg = await KnowmapConfigRepository(db).get(doc.knowmap_config_id)
        if cfg is None:
            if claim is None:
                await KnowmapDocumentRepository(db).set_status(
                    document_id=doc_id,
                    status=DocumentStatus.FAILED,
                )
            else:
                await KnowmapDocumentRepository(db).finish_claim(
                    document_id=doc_id,
                    claim=claim,
                    status=DocumentStatus.FAILED,
                )
            await db.commit()
            _log.warning("knowmap_ingest_document: config missing for %s", document_id)
            return "config missing"

        embedder = await build_knowmap_embedder(db, cfg)
        minio = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.root_access_key,
            secret_key=settings.minio.root_secret_key,
            secure=settings.minio.use_tls,
            region=settings.minio.region,
        )
        ingest = KnowledgeIngestWiring(db).knowmap_service(
            blob=MinioBlobStore(minio),
            embedder=embedder,
            bucket=settings.minio.bucket_knowmap_sources,
        )
        try:
            result = await ingest.process_document(document_id=doc_id, claim=claim)
            await db.commit()
        except Exception:
            await db.rollback()
            async with sm() as db2:
                current = await KnowmapDocumentRepository(db2).get(doc_id)
                if current is not None and current.status is not DocumentStatus.READY:
                    if claim is None:
                        await KnowmapDocumentRepository(db2).set_status(
                            document_id=doc_id,
                            status=DocumentStatus.FAILED,
                        )
                    else:
                        await KnowmapDocumentRepository(db2).finish_claim(
                            document_id=doc_id,
                            claim=claim,
                            status=DocumentStatus.FAILED,
                        )
                    await db2.commit()
            _log.exception("knowmap_ingest_document failed for %s", document_id)
            raise

    # F-5: the build waits for BOTH readiness and a clean scan verdict. READY is now
    # committed; re-read the scan verdict (the scan worker commits it on its own
    # connection) and enqueue only if clean. If the scan is still pending, the scan
    # worker's clean-verdict path enqueues once it observes READY — last writer wins.
    async with sm() as db2:
        fresh = await KnowmapDocumentRepository(db2).get(doc_id)
        fresh_cfg = await KnowmapConfigRepository(db2).get(cfg.id)
    if fresh is not None and fresh.scan_status is ScanStatus.CLEAN and fresh_cfg is not None:
        # F-12: target the corpus revision the ingest commit bumped (re-read fresh).
        await enqueue_knowmap_build(config_id=cfg.id, target_revision=fresh_cfg.corpus_revision)
    return f"status={result.status.value} document={document_id}"


knowmap_ingest_document.max_tries = 3  # type: ignore[attr-defined]


async def _enqueue_knowmap_ingest_after_clean(document_id: uuid.UUID) -> None:
    sm = get_sessionmaker()
    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(document_id)
    if (
        doc is None
        or doc.status is not DocumentStatus.INGESTING
        or doc.scan_status is not ScanStatus.CLEAN
        or doc.ingest_claim_token is None
    ):
        return
    await enqueue(
        "knowmap_ingest_document",
        document_id=str(document_id),
        ingest_attempt=doc.ingest_attempt,
        claim_token=str(doc.ingest_claim_token),
        _job_id=f"knowmap-ingest:{document_id}:{doc.ingest_attempt}",
        _queue_name=KNOWLEDGE_INGEST_QUEUE,
    )


async def knowmap_scan_document(ctx: dict[str, Any], *, document_id: str) -> str:
    """AV scan for a Knowledge Map document. Mirrors ``rag_scan_document``; a
    quarantine OR skipped verdict enqueues a rebuild so the un-scannable document
    leaves the buildable corpus (F-27)."""
    from app.config.settings import get_settings

    doc_id = uuid.UUID(document_id)
    sm = get_sessionmaker()

    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
    if doc is None:
        _log.warning("knowmap_scan_document: document %s not found", document_id)
        return "not_found"
    # F-12 (W1/W6): the buildable ready∧clean set only *changes* when a verdict
    # crosses the CLEAN boundary. A reconfirming CLEAN->CLEAN (a reindex rescan)
    # does not add the document, and a QUARANTINED/SKIPPED verdict on a document
    # that was never CLEAN removes nothing — so neither advances the revision nor
    # triggers a rebuild. Only a real membership change does.
    prior_clean = doc.scan_status is ScanStatus.CLEAN

    if not get_settings().security.file_scan_enabled:
        async with sm() as db, db.begin():
            from shared_kernel.auth.clients import now

            await KnowmapDocumentRepository(db).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.CLEAN, scan_at=now()
            )
        # F-5: scan disabled == an immediate clean verdict; route through the shared
        # clean-verdict enqueue so the deferred build still fires once READY.
        await _enqueue_build_on_clean(sm, doc_id, entered=not prior_clean)
        await _enqueue_knowmap_ingest_after_clean(doc_id)
        return "clean"

    from shared_kernel.scanning import ScanError, get_scanner
    from shared_kernel.storage.minio_client import get_minio_client

    scanner = get_scanner()
    if scanner is None:
        raise RuntimeError("file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set")

    settings = get_settings()
    if doc.size_bytes > settings.security.clamav_max_scan_bytes:
        from shared_kernel.auth.clients import now as _now2

        async with sm() as db2, db2.begin():
            await KnowmapDocumentRepository(db2).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.SKIPPED, scan_at=_now2()
            )
        # F-27: over-size is immediately terminal (no retry). Rebuild only if the
        # document was already CLEAN (hence possibly built) — a fresh over-size
        # document was never in the buildable set, so no rebuild is needed (F-12 W6).
        if prior_clean:
            await _enqueue_rebuild_for_config(sm, doc.knowmap_config_id)
        return "skipped:too_large"

    bucket, _, key = doc.minio_path.partition("/")
    minio = get_minio_client()
    data = await minio.get_object(bucket=bucket, key=key)

    try:
        result = await scanner.scan(data)
    except ScanError:
        _log.exception("knowmap_scan_document: ClamAV error for document %s", document_id)
        from shared_kernel.auth.clients import now as _now

        # Mark SKIPPED immediately on every attempt, so an unscannable document is
        # excluded from the buildable/retrieval set right away and is never left
        # non-terminal by a retry that is interrupted before it completes. The task
        # still re-raises so arq retries the scan; if a later attempt recovers a
        # CLEAN verdict, the clean-verdict path below re-adds the document.
        async with sm() as db2, db2.begin():
            await KnowmapDocumentRepository(db2).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.SKIPPED, scan_at=_now()
            )
        # F-27 (Q-2): evict on the CLEAN->SKIPPED transition, mirroring the
        # QUARANTINED path. ``prior_clean`` is read fresh from ``scan_status`` at
        # entry (before this write), so it is True only on the attempt that first
        # removes a previously-CLEAN (hence possibly built) document from the
        # buildable set — later retries read SKIPPED and never re-enqueue. A
        # never-CLEAN document has no triples in the graph to evict (F-12 W6).
        # (The earlier ``job_try >= _SCAN_MAX_TRIES`` guard could never fire: the
        # first attempt's SKIPPED write made ``prior_clean`` read False by the
        # final attempt, so the eviction never ran.)
        if prior_clean:
            await _enqueue_rebuild_for_config(sm, doc.knowmap_config_id)
        raise

    from shared_kernel import audit
    from shared_kernel.auth.clients import now

    scan_status = ScanStatus.CLEAN if result.clean else ScanStatus.QUARANTINED
    if not result.clean:
        _log.warning(
            "knowmap_scan_document: document %s quarantined — threat=%s",
            document_id,
            result.threat_name,
        )

    async with sm() as db:
        cfg = await KnowmapConfigRepository(db).get(doc.knowmap_config_id)
        async with db.begin():
            await KnowmapDocumentRepository(db).mark_scan(
                document_id=doc_id, scan_status=scan_status, scan_at=now()
            )
            if scan_status is ScanStatus.QUARANTINED:
                await audit.emit(
                    db,
                    audit.AuditEvent(
                        action="knowmap.document.quarantined",
                        resource_type="knowmap_document",
                        resource_id=doc_id,
                        metadata={
                            "scan_status": scan_status.value,
                            "threat_name": result.threat_name,
                        },
                    ),
                )
    # A quarantine of a document that was CLEAN (hence possibly built) changes the
    # buildable corpus → rebuild so its triples leave the graph (retrieval already
    # hides them via the allowed-doc gate). Advance the revision (F-12 W1) so the
    # removal build is never dropped. A never-CLEAN document has no triples in the
    # graph, so nothing to evict (F-12 W6).
    if scan_status is ScanStatus.QUARANTINED and cfg is not None and prior_clean:
        await _bump_and_enqueue_build(sm, cfg.id)
    elif scan_status is ScanStatus.CLEAN:
        # F-5: a clean verdict enqueues the deferred build — but only once the
        # document is READY (re-read fresh; last writer wins with the index worker).
        # Advance the revision only when the document is newly *entering* the
        # ready∧clean set; a CLEAN->CLEAN reconfirm is handled by the ingest side.
        await _enqueue_build_on_clean(sm, doc_id, entered=not prior_clean)
        await _enqueue_knowmap_ingest_after_clean(doc_id)
    return scan_status.value


knowmap_scan_document.max_tries = _SCAN_MAX_TRIES  # type: ignore[attr-defined]


def _make_knowmap_embedder_factory(db: AsyncSession) -> EmbedderFactory:
    """EmbedderFactory selecting the key by the config's pinned embedding provider
    (Phase 2a D2) via the shared ``resolve_pinned_embed_key`` — the build,
    ingest, and retrieval paths resolve identically and cannot drift."""
    router = build_router(db)

    async def _factory(cfg: ConfigLike) -> ResolvedEmbedder:
        provider, model, key_id = await resolve_pinned_embed_key(db, cfg)
        embedder = router_embedder_for(
            router=router,
            key_id=key_id,
            project_id=cfg.project_id,
            provider=provider,
            model=model,
        )
        return ResolvedEmbedder(embedder=embedder, provider=provider, model=model)

    return _factory


async def knowmap_build(
    ctx: dict[str, Any],
    *,
    config_id: str,
    triggered_by: str = "manual",
    target_revision: int | None = None,
) -> str:
    """Run a full Knowledge Map build for one config over the shared 2PC engine.

    Reuses the GraphRAG builder / Neo4j driver / snapshot + lock stores unchanged
    (R11.15); only the extractor (:class:`DocTripleExtractor`), the delta loader
    (:class:`DocDeltaLoader`), and the Qdrant collection prefix (``knowmap``) are
    forked. The Neo4j subgraph is scoped by the opaque config id; entity vectors
    land in ``knowmap_{project_id}``.

    ``target_revision`` (F-12) is the ``corpus_revision`` this build was enqueued
    for. On success it is recorded as ``built_corpus_revision``; if the corpus
    advanced past it *during* the build (a mutation committed after the delta
    snapshot), a single follow-up build is enqueued for the newer revision so no
    committed change is ever left unbuilt. ``None`` for a legacy in-flight job
    enqueued before this deploy — those skip the re-check and complete normally.
    """
    _ = ctx
    from qdrant_client import AsyncQdrantClient

    from app.config.settings import get_settings
    from contexts.knowledge.infrastructure.channels import knowmap_channel
    from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
    from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
    from contexts.knowledge.infrastructure.redis_lock import (
        RedisBuildLockStore,
        RedisSnapshotStore,
    )

    cfg_id = uuid.UUID(config_id)
    settings = get_settings()

    neo4j = Neo4jAsyncDriver(uri=settings.neo4j.url, auth=(settings.neo4j.user, settings.neo4j.password))
    qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
    try:
        vector_store = GraphRagVectorStore(qclient, prefix="knowmap")
        sm = get_sessionmaker()
        async with sm() as db:
            configs = KnowmapConfigRepository(db)
            cfg = await configs.get(cfg_id)
            if cfg is None:
                _log.warning("knowmap_build: config %s not found", config_id)
                return f"config {config_id} not found"

            builder = GraphRagBuilder(
                db=db,
                neo4j=neo4j,
                vector_store=vector_store,
                extractor=DocTripleExtractor(router=build_router(db)),
                lock_store=RedisBuildLockStore(),
                snapshot_store=RedisSnapshotStore(),
                delta_loader=DocDeltaLoader(),
                embedder_factory=_make_knowmap_embedder_factory(db),
                configs=configs,
                channel_fn=knowmap_channel,
            )
            try:
                # F-6: Knowledge Map builds are full-corpus and use replacement
                # semantics — the loader re-reads the whole surviving corpus each
                # build, so relations/entities/vectors absent from the current
                # corpus are removed and evidence/provenance is recomputed to the
                # live corpus. Concept Map delta builds pass replace=False.
                result = await builder.run(config_id=cfg_id, triggered_by=triggered_by, replace=True)
                await db.commit()
            except Exception:
                _log.exception("knowmap_build failed config=%s", config_id)
                raise
            _log.info(
                "knowmap_build done config=%s state=%s triples=%d entities=%d",
                config_id,
                result.state.value,
                result.triples_written,
                result.entities_written,
            )
        # F-12: record the processed revision and enqueue one follow-up if the
        # corpus advanced during the build (a mutation committed after the delta
        # snapshot). Runs in its own session — the shared builder stays
        # enqueue-free so layer boundaries hold and Concept Maps are unaffected.
        # Best-effort: the build already committed its terminal state above, so a
        # transient error in this post-build bookkeeping must not fail the job and
        # trigger arq to re-run the entire (already-successful) build. A missed
        # follow-up is recovered by the next document change or a manual rebuild.
        try:
            await _finalize_build_revision(
                sm, cfg_id, target_revision, succeeded=result.state is BuildState.IDLE
            )
        except Exception:
            _log.exception("knowmap_build: post-build revision finalize failed config=%s", config_id)
        return (
            f"state={result.state.value} "
            f"triples={result.triples_written} entities={result.entities_written}"
        )
    finally:
        await neo4j.close()
        await qclient.close()


async def _finalize_build_revision(
    sm: Any,
    config_id: uuid.UUID,
    target_revision: int | None,
    *,
    succeeded: bool,
) -> int | None:
    """Record the built revision and re-enqueue if the corpus advanced (F-12).

    On a successful build, stamp ``built_corpus_revision = target_revision`` and
    re-read the current ``corpus_revision``; if it advanced past the target while
    the build ran, enqueue exactly one follow-up for the newer revision (which
    itself deduplicates on that revision, so the chain terminates once the built
    revision catches up). No-op for a failed build or a legacy job with no
    ``target_revision``. Returns the follow-up revision enqueued, or ``None``."""
    if not succeeded or target_revision is None:
        return None
    async with sm() as db:
        configs = KnowmapConfigRepository(db)
        async with db.begin():
            await configs.set_built_corpus_revision(config_id, target_revision)
        current = await configs.get(config_id)
    if current is not None and current.corpus_revision > target_revision:
        await enqueue_knowmap_build(config_id=config_id, target_revision=current.corpus_revision)
        return current.corpus_revision
    return None


__all__ = [
    "knowmap_build",
    "knowmap_ingest_document",
    "knowmap_revision_sweep",
    "knowmap_scan_document",
]
