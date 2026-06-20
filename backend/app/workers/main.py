"""Arq worker entrypoint.

Task registry:
  - `noop`                        — liveness smoke
  - `file_scan_requested`         — F.5 attachment AV pass (no-op by default)
  - `chat_export`                 — F.10 chat export → JSON manifest in MinIO
  - `retention_sweep`             — I.4 nightly consolidated retention sweep (cron)
  - `key_usage_threshold_sample`  — D.8 80% hourly-limit sampler (every 30 s)
  - `rag_ingest_document`         — E.6 off-request RAG indexing for tus uploads
  - `agent_fs_gc`                 — E.10 nightly Docker volume GC (60-day retention)

Background tasks (started in `on_startup`, stopped in `on_shutdown`):
  - key-revocation listener  — ASYNC-2 / D.7 DEK cache invalidation
  - A2A consumer supervisor  — ASYNC-1 / G.1 drains every agent inbox stream
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

import app.db_registry as _db_registry  # noqa: F401 — table imports
from app.config.settings import get_settings
from app.workers.agent_fs_gc import run_once as _agent_fs_gc_run_once
from app.workers.tasks.advisory import daily_org_advisory_snapshot
from app.workers.tasks.approvals import drive_approver_turn
from app.workers.tasks.conversation import (
    chat_export,
    compact_chatroom,
    file_scan_requested,
)
from app.workers.tasks.graphrag import graphrag_build, graphrag_reconcile
from app.workers.tasks.orchestration import (
    approval_timeout,
    evaluate_silence,
    make_dlq_audit_callback,
    wakeup_agent,
    wakeup_refresh,
)
from app.workers.tasks.rag import rag_ingest_document, rag_scan_document
from app.workers.tasks.retention import retention_sweep
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
    workflow_subagent_complete,
    workflow_subagent_timeout,
)
from app.workers.tasks.workflow_watchdog import workflow_watchdog
from contexts.keys.application.threshold_worker import sample_once as _threshold_sample_once
from contexts.keys.infrastructure import revocation_listener
from contexts.orchestration.application.a2a_consumer import A2AConsumerSupervisor
from contexts.orchestration.application.a2a_handler import handle_envelope
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.logging.setup import configure_logging


async def noop(ctx: dict[str, Any]) -> str:
    return "ok"


async def agent_fs_gc(ctx: dict[str, Any]) -> dict[str, int]:
    """E.10 — nightly GC of per-agent Docker volumes (60-day retention)."""
    _ = ctx
    removed = await _agent_fs_gc_run_once()
    return {"removed": removed}


async def sandbox_orphan_cleanup(ctx: dict[str, Any]) -> dict[str, int]:
    """B2-4 — periodic cleanup of orphaned sandbox containers."""
    _ = ctx
    from contexts.agents.infrastructure.sandbox.docker_runsc import (
        docker_runsc_sandbox_from_settings,
    )

    sandbox = docker_runsc_sandbox_from_settings()
    removed = await sandbox.cleanup_orphan_containers()
    return {"removed": removed}


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
    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
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
    )
    ctx["_a2a_supervisor"] = supervisor
    ctx["_a2a_task"] = asyncio.create_task(
        supervisor.run(),
        name="a2a-consumer-supervisor",
    )


async def _shutdown(ctx: dict[str, Any]) -> None:
    """Wind down the long-lived background tasks started in `_startup`."""
    supervisor = ctx.get("_a2a_supervisor")
    if supervisor is not None:
        await supervisor.stop()
    # Cancel the listener tasks before Arq tears the loop down; their
    # cancellation paths unsubscribe / close Redis pub/sub cleanly.
    for key in ("_a2a_task", "_revocation_task"):
        task = ctx.get(key)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        noop,
        file_scan_requested,
        chat_export,
        wakeup_agent,
        evaluate_silence,
        wakeup_refresh,
        approval_timeout,
        compact_chatroom,
        drive_approver_turn,
        run_workflow_step,
        retry_workflow_node,
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
        retention_sweep,
        daily_org_advisory_snapshot,
        key_usage_threshold_sample,
        graphrag_build,
        graphrag_reconcile,
        rag_ingest_document,
        rag_scan_document,
        agent_fs_gc,
        sandbox_orphan_cleanup,
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
        # Every 30 seconds — D.8 80% hourly-limit sampler (R7.11).
        cron(key_usage_threshold_sample, second={0, 30}, run_at_startup=False),
        # 05:00 UTC daily — per-agent Docker volume GC (E.10 / R12.03, 60-day retention).
        cron(agent_fs_gc, hour=5, minute=0, run_at_startup=False),
        # Every minute — heal GraphRAG 2PC drift (M.5.4 / R11.04): configs stuck
        # in FAILED_COMPENSATING. arq's cron lock keeps it singleton across replicas.
        cron(graphrag_reconcile, minute=set(range(60)), run_at_startup=False),
        # Every 5 minutes — orphan sandbox container cleanup (B2-4).
        cron(
            sandbox_orphan_cleanup,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
    ]
