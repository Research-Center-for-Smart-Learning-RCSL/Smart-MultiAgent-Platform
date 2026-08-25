"""Arq worker entrypoint.

Task registry:
  - `noop`                        — liveness smoke
  - `file_scan_requested`         — F.5 attachment AV pass (no-op by default)
  - `extract_attachment_text`     — best-effort chat attachment text extraction
  - `chat_export`                 — F.10 chat export → JSON manifest in MinIO
  - `retention_sweep`             — I.4 nightly consolidated retention sweep (cron)
  - `key_usage_threshold_sample`  — D.8 80% hourly-limit sampler (every 30 s)
  - `rag_ingest_document`         — E.6 off-request RAG indexing for tus uploads
  - `agent_fs_gc`                 — E.10 nightly agent volume + workspace GC (60-day
                                    retention; dry-run until SMAP_AGENT_FS_GC_ARMED)
  - `agent_turn_reaper`           — per-minute sweep for turns killed by SIGKILL,
                                    the one cleanup no in-process handler survives

Background tasks (started in `on_startup`, stopped in `on_shutdown`):
  - key-revocation listener  — ASYNC-2 / D.7 DEK cache invalidation
  - A2A consumer supervisor  — ASYNC-1 / G.1 drains every agent inbox stream
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings

import app.db_registry as _db_registry  # noqa: F401 — table imports
from app.config.settings import get_settings
from app.workers.agent_fs_gc import AGENT_FS_GC_TIMEOUT_S
from app.workers.agent_fs_gc import sweep_once as _agent_fs_gc_sweep_once
from app.workers.agent_fs_gc import sweep_report_dict as _agent_fs_gc_report_dict
from app.workers.tasks.activities import (
    activities_watchdog,
    expire_group_proposals,
    validate_activity_submission,
)
from app.workers.tasks.advisory import daily_org_advisory_snapshot
from app.workers.tasks.approvals import approval_gate_announce, drive_approver_turn
from app.workers.tasks.conversation import (
    chat_export,
    compact_chatroom,
    extract_attachment_text,
    file_scan_requested,
)
from app.workers.tasks.graphrag import (
    GRAPHRAG_BUILD_TIMEOUT_S,
    graphrag_build,
    graphrag_reconcile,
    graphrag_silence_sweep,
)
from app.workers.tasks.knowledge_ingest import knowledge_ingest_reconcile
from app.workers.tasks.knowmap import (
    KNOWMAP_BUILD_TIMEOUT_S,
    knowmap_build,
    knowmap_ingest_document,
    knowmap_revision_sweep,
    knowmap_scan_document,
)
from app.workers.tasks.orchestration import (
    WAKEUP_TURN_TIMEOUT_S,
    approval_timeout,
    evaluate_silence,
    make_dlq_audit_callback,
    wakeup_agent,
    wakeup_refresh,
)
from app.workers.tasks.prompt_assistant import prompt_assistant_turn
from app.workers.tasks.rag import rag_ingest_document, rag_scan_document
from app.workers.tasks.retention import retention_sweep
from app.workers.tasks.skills import skill_export_bundle, skill_import_bundle, skill_scan_file
from app.workers.tasks.turn_reaper import agent_turn_reaper
from app.workers.tasks.workflow_approvals import (
    workflow_instruct_timeout,
    workflow_resume_approval,
    workflow_resume_instruct,
)
from app.workers.tasks.workflow_cron import workflow_cron_scheduler
from app.workers.tasks.workflow_signals import (
    run_triggered_workflow,
    workflow_event_resume,
    workflow_event_timeout,
    workflow_signal,
    workflow_variable_signal,
)
from app.workers.tasks.workflow_steps import (
    retry_workflow_node,
    run_workflow_step,
    workflow_cancel_a2a_calls,
    workflow_subagent_complete,
    workflow_subagent_timeout,
)
from app.workers.tasks.workflow_watchdog import workflow_watchdog
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.keys.application.threshold_worker import sample_once as _threshold_sample_once
from contexts.keys.infrastructure import revocation_listener
from contexts.orchestration.application.a2a_consumer import A2AConsumerSupervisor
from contexts.orchestration.application.a2a_handler import handle_envelope
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.logging.setup import configure_logging
from shared_kernel.queue_names import KNOWLEDGE_INGEST_QUEUE, KNOWLEDGE_SCAN_QUEUE


async def noop(ctx: dict[str, Any]) -> str:
    return "ok"


async def agent_fs_gc(ctx: dict[str, Any]) -> dict[str, int]:
    """E.10 — nightly GC of per-agent volumes + workspace objects (60-day retention).

    Returns the full sweep report, not just a count: the worker is dry-run by
    default, so `would_purge` above zero with `removed` at zero is the normal
    unarmed state and an operator needs to see it in the job result.
    """
    _ = ctx
    return _agent_fs_gc_report_dict(await _agent_fs_gc_sweep_once())


async def sandbox_orphan_cleanup(ctx: dict[str, Any]) -> dict[str, int]:
    """B2-4 — periodic cleanup of orphaned sandbox containers."""
    _ = ctx
    from contexts.agents.infrastructure.sandbox.docker_runsc import (
        docker_runsc_sandbox_from_settings,
    )

    sandbox = docker_runsc_sandbox_from_settings()
    removed = await sandbox.cleanup_orphan_containers()
    # Backstop for live kernels whose owning process crashed before idle-reaping.
    kernels = await sandbox.cleanup_orphan_kernels()
    return {"removed": removed, "kernels": kernels}


async def key_usage_threshold_sample(ctx: dict[str, Any]) -> int:
    """D.8 — 80% hourly-limit sampler. Runs every 30 s via cron."""
    _ = ctx
    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        return await _threshold_sample_once(session)


def _arq_redis_settings() -> RedisSettings:
    s = get_settings().redis
    base = RedisSettings.from_dsn(s.dsn)
    return RedisSettings(
        host=base.host,
        port=base.port,
        unix_socket_path=base.unix_socket_path,
        database=base.database,
        password=base.password,
        ssl=base.ssl,
        conn_timeout=s.socket_connect_timeout,
        conn_retries=3,
        conn_retry_delay=1,
    )


def _redis_alive() -> bool:
    """Sync Redis PING — fails fast if Arq's broker is unreachable.

    Arq jobs cannot be dequeued without Redis, so a process that's running
    but disconnected from Redis is *not* healthy. The previous healthcheck
    only verified the sidecar HTTP server, which masked broker outages.
    """
    try:
        import redis as _redis_sync

        s = get_settings().redis
        client = _redis_sync.Redis.from_url(
            s.dsn,
            socket_connect_timeout=s.socket_connect_timeout,
            socket_timeout=s.socket_timeout,
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


class _HealthzHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        if not _redis_alive():
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"redis-unreachable"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def _start_healthz_sidecar() -> None:
    port = int(os.environ.get("SMAP_WORKER_HEALTHZ_PORT", "8001"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthzHandler)  # noqa: S104 — sidecar binds in-container only
    Thread(target=server.serve_forever, daemon=True, name="worker-healthz").start()


async def _live_agents(agent_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Liveness filter for the A2A consumer supervisor (F-20): the DB is the
    authority on which discovered inbox streams still have a live agent."""
    if not agent_ids:
        return set()
    sm = get_sessionmaker()
    async with sm() as session:
        return await AgentsFacade(session).filter_live_agents(agent_ids)


async def _startup(ctx: dict[str, Any]) -> None:
    configure_logging(get_settings().logging)
    _start_healthz_sidecar()
    # ASYNC-2: punch revoked / carry-withdrawn DEKs out of this worker's
    # in-process provider_router cache the moment the pub/sub event fires.
    ctx["_revocation_task"] = asyncio.create_task(
        revocation_listener.run(),
        name="key-revocation-listener",
    )
    # ASYNC-1: drain every agent's A2A inbox stream. Without this every A2A
    # message sits in Redis unread and every synchronous `call` blocks to
    # timeout. The supervisor wires the DLQ audit callback (G.9).
    supervisor = A2AConsumerSupervisor(
        handle_envelope,
        on_dlq=make_dlq_audit_callback(),
        liveness=_live_agents,
    )
    ctx["_a2a_supervisor"] = supervisor
    ctx["_a2a_task"] = asyncio.create_task(
        supervisor.run(),
        name="a2a-consumer-supervisor",
    )
    # Reap idle Code-Interpreter kernels so long-lived sandbox containers don't
    # accumulate between the hourly orphan sweep.
    from contexts.agents.infrastructure.sandbox.docker_runsc import reap_idle_kernels

    ctx["_kernel_reaper_task"] = asyncio.create_task(
        reap_idle_kernels(),
        name="code-exec-kernel-reaper",
    )


async def _knowledge_startup(ctx: dict[str, Any]) -> None:
    configure_logging(get_settings().logging)
    _start_healthz_sidecar()
    ctx["_revocation_task"] = asyncio.create_task(
        revocation_listener.run(),
        name="key-revocation-listener",
    )


async def _knowledge_shutdown(ctx: dict[str, Any]) -> None:
    task = ctx.get("_revocation_task")
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _shutdown(ctx: dict[str, Any]) -> None:
    """Wind down the long-lived background tasks started in `_startup`."""
    supervisor = ctx.get("_a2a_supervisor")
    if supervisor is not None:
        await supervisor.stop()
    # Cancel the listener tasks before Arq tears the loop down; their
    # cancellation paths unsubscribe / close Redis pub/sub cleanly.
    for key in ("_a2a_task", "_revocation_task", "_kernel_reaper_task"):
        task = ctx.get(key)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    # Best-effort: tear down any kernels this worker still holds.
    with suppress(Exception):
        from contexts.agents.infrastructure.sandbox.docker_runsc import shutdown_all_kernels

        await shutdown_all_kernels()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        noop,
        file_scan_requested,
        extract_attachment_text,
        chat_export,
        # A turn is not retry-safe: it commits its reply with post-commit work
        # still to run, and the turn lock's `finally` releases during the
        # cancellation unwind, so arq's worker-wide `retry_jobs` default would
        # let a re-run re-assemble history that already contains the reply and
        # post a second one. `max_tries=1` is arq's documented "prevent
        # retrying" and is enforced before the job body runs. The scoped timeout
        # is a runaway backstop over the turn lock TTL — see
        # WAKEUP_TURN_TIMEOUT_S for why it is not tightened below it.
        func(wakeup_agent, name="wakeup_agent", timeout=WAKEUP_TURN_TIMEOUT_S, max_tries=1),
        evaluate_silence,
        wakeup_refresh,
        approval_timeout,
        approval_gate_announce,
        compact_chatroom,
        drive_approver_turn,
        run_workflow_step,
        retry_workflow_node,
        workflow_cancel_a2a_calls,
        workflow_event_timeout,
        workflow_subagent_timeout,
        workflow_subagent_complete,
        workflow_cron_scheduler,
        workflow_signal,
        workflow_variable_signal,
        workflow_event_resume,
        run_triggered_workflow,
        workflow_resume_approval,
        workflow_resume_instruct,
        workflow_instruct_timeout,
        workflow_watchdog,
        agent_turn_reaper,
        validate_activity_submission,
        activities_watchdog,
        expire_group_proposals,
        retention_sweep,
        daily_org_advisory_snapshot,
        key_usage_threshold_sample,
        # D3: graphrag_build gets a longer, scoped job timeout so the lock (not
        # the timeout) is the single-writer authority; other lanes keep the
        # default worker job_timeout.
        func(graphrag_build, name="graphrag_build", timeout=GRAPHRAG_BUILD_TIMEOUT_S),
        graphrag_reconcile,
        graphrag_silence_sweep,
        knowmap_revision_sweep,
        knowledge_ingest_reconcile,
        skill_scan_file,
        skill_import_bundle,
        skill_export_bundle,
        # knowmap_build mirrors graphrag_build's scoped timeout: the build lock is
        # the single-writer authority, so the job timeout has TTL headroom.
        func(knowmap_build, name="knowmap_build", timeout=KNOWMAP_BUILD_TIMEOUT_S),
        # Scoped timeout, mirroring graphrag_build: the first armed sweep can
        # reclaim years of leaked artifacts and must not be killed part-way
        # through by the default job_timeout.
        func(agent_fs_gc, name="agent_fs_gc", timeout=AGENT_FS_GC_TIMEOUT_S),
        sandbox_orphan_cleanup,
        prompt_assistant_turn,
    ]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = _arq_redis_settings()
    job_timeout = 600
    max_jobs = 50
    keep_result = 3600
    cron_jobs: ClassVar[list[Any]] = [
        # 03:30 UTC daily — single consolidated retention sweep (I.4). Covers
        # message purge (R13.15) and 90-day workflow-run archival (H.6); the
        # former duplicate `retention_purge` / `archive_workflow_runs` crons
        # were removed (ASYNC-4 — one retention path per table).
        cron(retention_sweep, hour=3, minute=30, run_at_startup=False),
        # Every 30 seconds — silence trigger sweep (G.3 / R15.02).
        cron(evaluate_silence, second={0, 30}, run_at_startup=False),
        # Hourly — wakeup config refresh to authored values (G.5 / R15.09).
        cron(wakeup_refresh, minute=0, run_at_startup=False),
        # 02:00 UTC daily — per-tenant advisory snapshot (I.8).
        cron(daily_org_advisory_snapshot, hour=2, minute=0, run_at_startup=False),
        # Every minute — cron trigger scheduler (H.4).
        cron(workflow_cron_scheduler, minute=set(range(60)), run_at_startup=False),
        # Every minute — workflow timeout watchdog (K.4): fail runs past their
        # run_max_seconds / idle_max_seconds budgets.
        cron(workflow_watchdog, minute=set(range(60)), run_at_startup=False),
        # Every minute — stranded agent turns: a turn killed by SIGKILL runs no
        # cleanup of its own and `wakeup_agent` is `max_tries=1`, so this sweep
        # is the only thing that unsticks the room. arq's cron lock keeps it
        # singleton across replicas.
        cron(agent_turn_reaper, minute=set(range(60)), run_at_startup=False),
        # Every minute — activities validation watchdog (R30.06): sweep stalled
        # pending mcp/webhook validations (or dropped enqueues) to error.
        cron(activities_watchdog, minute=set(range(60)), run_at_startup=False),
        # Every five minutes — group-proposal expiry ([R30.41]). The BACKSTOP
        # only: ending a round expires its proposals in the same transaction as
        # the end, so this catches the room whose round nobody ever ended. A
        # per-minute tick would buy nothing, because the deadline it enforces is
        # hours away.
        cron(
            expire_group_proposals,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
        # Every 30 seconds — D.8 80% hourly-limit sampler (R7.11).
        cron(key_usage_threshold_sample, second={0, 30}, run_at_startup=False),
        # 05:00 UTC daily — per-agent volume + workspace GC (E.10 / R12.03, 60-day
        # retention). Ordering-independent by design: it reclaims by enumerating
        # what exists, so it does not care that retention_sweep ran at 03:30.
        cron(agent_fs_gc, hour=5, minute=0, run_at_startup=False, timeout=AGENT_FS_GC_TIMEOUT_S),
        # Every minute — heal GraphRAG 2PC drift (M.5.4 / R11.04): configs stuck
        # in FAILED_COMPENSATING. arq's cron lock keeps it singleton across replicas.
        cron(graphrag_reconcile, minute=set(range(60)), run_at_startup=False),
        # Every minute — Knowledge Map revision divergence sweep (F-4 / R11.12):
        # re-offer builds for committed corpus revisions that the best-effort
        # finalize/enqueue path dropped. arq's cron lock keeps it singleton across
        # replicas; the revision-keyed job id makes a repeated offer a no-op.
        cron(knowmap_revision_sweep, minute=set(range(60)), run_at_startup=False),
        # Every minute — reclaim knowledge-ingest leases whose worker disappeared.
        cron(knowledge_ingest_reconcile, minute=set(range(60)), run_at_startup=False),
        # Every minute — Concept Map silence-trigger sweep (F-4 / R11.02): fire a
        # graphrag_build for maps whose coverage has been idle for silence_minutes.
        # arq's cron lock keeps it singleton; keep_result backs the _job_id dedup.
        cron(graphrag_silence_sweep, minute=set(range(60)), run_at_startup=False),
        # Every 5 minutes — orphan sandbox container cleanup (B2-4).
        cron(
            sandbox_orphan_cleanup,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
    ]


class KnowledgeScanWorkerSettings:
    functions: ClassVar[list[Any]] = [rag_scan_document, knowmap_scan_document]
    on_startup = _knowledge_startup
    on_shutdown = _knowledge_shutdown
    redis_settings = _arq_redis_settings()
    queue_name = KNOWLEDGE_SCAN_QUEUE
    job_timeout = 20 * 60
    max_jobs = 2
    keep_result = 3600


class KnowledgeIngestWorkerSettings:
    functions: ClassVar[list[Any]] = [rag_ingest_document, knowmap_ingest_document]
    on_startup = _knowledge_startup
    on_shutdown = _knowledge_shutdown
    redis_settings = _arq_redis_settings()
    queue_name = KNOWLEDGE_INGEST_QUEUE
    job_timeout = 30 * 60
    max_jobs = 1
    keep_result = 3600
